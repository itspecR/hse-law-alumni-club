# Правила проекта Клуба выпускников

Глобальные правила Codex дополняются контекстом этого репозитория. Перед правкой
проверь ветку, рабочее дерево и относящиеся к задаче файлы. Существующие
незакоммиченные изменения не сбрасывай и не включай в свой коммит без ревью.

## Устройство проекта

- `frontend` – Django web и Jinja2; `backend` – Django API; `data` – общие
  справочники; `scripts` –
  миграции, bootstrap, управление сотрудниками и резервные копии.
- Серверные маршруты и логика области находятся в `backend/club_api/modules`;
  общие SQL-механизмы – в `backend/club_api/db`. Подготовка страниц находится
  в `frontend/club_web/pages.py`, шаблоны – в `templates`, браузерные модули – в `browser`.
- Тесты и фикстуры находятся в `tests` соответствующего пакета, вне `src`.
  Docker-файлы собраны в `deploy`. В `scripts` остаются команды оператора;
  помощники, проверки и диагностика выделены в `lib`, `checks`, `diagnostics`.
  Полная карта – `docs/development/repository-structure.md`.
- Новые установки используют нативную MariaDB Ubuntu, Django и nginx через
  `deploy/compose.mariadb.yml`. Установщик и обслуживание описаны в
  `docs/operations/mariadb-migration.md`. Существующие установки версии 1
  сохраняют PostgreSQL/Caddy; автоматическая смена базы запрещена.
  Directus исключён по решению владельца; основание – `docs/decisions/cms-options.md`.
  Источник операционных команд и порядка отката – `docs/operations/deploy-runbook.md`.
- Порт API не публикуется на хосте: входящие запросы и проверка IP проходят
  через Caddy или nginx выбранного контура. Не добавляй `ports` для API без пересмотра границы доверия.
- Для интерфейса соблюдай решения `DESIGN.md`. Не перезаписывай его генератором.
- В собственном коде не добавляй комментарии и поясняющие docstring. Настройки
  инструментов задавай в конфигурации. Обязательные лицензии стороннего кода
  и исполняемые заголовки скриптов сохраняй.

## Инварианты выпуска

- Цены, скидки, суммы и права доступа окончательно проверяются в API. Данные
  корзины и запросы браузера нельзя считать источником цены или роли.
- Платёжный вебхук проверяет адрес отправителя, затем запрашивает состояние
  платежа у ЮKassa и сверяет сумму заявки. Изменения этого пути проверяй
  тестами на подделку, повторную и одновременную доставку.
- Хеши паролей, секреты JWT, `POINTS_SERVICE_TOKEN`, платёжные и почтовые ключи
  остаются на сервере.
  Не коммить `.env`, дампы БД, uploads и данные участников.
- Переписку поддержки читают и изменяют только администраторы с ролью `admin`;
  редактору `editor` доступны сводные данные бота без текста обращений.
- Рабочая конфигурация требует `APP_ENV=production` и `SEED_DEMO=false`.
  Внутрипроцессные блокировки и cron предполагают один экземпляр API.
  Перед масштабированием устрани эту зависимость.
- Сборка и локальный тест не доказывают работу домена, TLS, почты, Telegram,
  оплаты и восстановления копии на целевом сервере.
- Сервис `migrate` применяет SQL-миграции и индексы до нативного bootstrap и API.
  `scripts/apply-indexes.sh` нужен для ручного восстановления схемы.
  В MariaDB применяются миграции `backend/club_api/db/migrations` командой оператора `migrate`.
  `/api/ready` проверяет выбранную базу и обязательные таблицы приложения.
- Совместимые имена `directus_users`, `directus_roles`, `directus_files` и тома
  `directus_uploads` сохраняют существующие UUID, хеши и ссылки; это не работающая CMS.
  Миграции дополняют схему без сброса данных. При дубликатах `alumni.user_id` запуск
  останавливается; удалять записи автоматически запрещено.
- SQL-роль API не меняет роли пользователей и не читает `club_settings` или прочие
  metadata CMS. Пароль читает/пишет только специализированный серверный auth-модуль;
  общий слой данных исключает секретные поля. Сотрудниками управляет оператор.
- Повторный bootstrap не заменяет пароли, роли, настройки или контент. Старые
  SSO/MFA-аккаунты нельзя незаметно переводить на вход по одному паролю.
- Bootstrap загружает `club_ops/data/demo.json` и отдельный каталог только при `APP_ENV=development` и
  `SEED_DEMO=true`. Статическое зеркало импортирует сиды отдельно. Рабочая выдача
  контента не подставляет сиды вместо пустых полей. Обычная web-сборка и API-образ
  не содержат демоперехватчика и сидов.
- Собственные пакеты поставляются в финальных образах как скомпилированный код
  с рабочими зависимостями. Исходники проекта, его тесты, sourcemap, отчёты, env
  и кэши инструментов в них не добавляются. Файлы сторонних Python-пакетов могут
  включать исходники, типы и map; их лицензии сохраняются.

## Проверки перед PR

Запусти относящиеся к изменению тесты. Для изменения выпуска и зависимостей:

```bash
uv sync --directory backend --frozen
uv sync --directory scripts --frozen
uv sync --directory frontend --frozen
uv run --directory frontend playwright install chromium
uv run --directory backend python manage.py check
uv run --directory frontend python manage.py check
uv run --directory backend ruff check club_api tests/python ../scripts/club_ops
uv run --directory frontend ruff check club_web tests/python
uv run --directory frontend python -m club_web.build --output /tmp/club-web
uv run --directory frontend python ../scripts/checks/check-web-build.py /tmp/club-web
uv run --directory frontend python ../scripts/checks/check-comments.py
uv run --directory frontend python ../scripts/checks/check-text-style.py
uv run --directory frontend python ../scripts/tests/test-text-style.py
uv run --directory frontend pytest -q
uv export --directory backend --frozen --no-dev --no-emit-project --format requirements-txt --output-file /tmp/club-requirements.txt
uv run --directory backend --frozen pip-audit -r /tmp/club-requirements.txt --no-deps --disable-pip
bash scripts/checks/audit-dependencies.sh security-reports
uv run --directory frontend python ../scripts/tests/test-security-inventory.py
docker compose --env-file .env.example config --quiet
bash scripts/tests/test-integration.sh
```

Для mysqlclient нужны заголовки Connector/C и pkg-config. Для MariaDB выполните
`scripts/tests/test-mariadb-integration.sh` с `CLUB_TEST_DATABASE_URL` отдельной
локальной пустой базы `django_migration_test`. Тесты очищают её таблицы; рабочую
базу использовать запрещено. Прежнюю PostgreSQL и uploads при переносе сохраняйте
до проверки нового контура и восстановления копии.

Для изменённых shell-скриптов дополнительно используй `bash -n`. Для видимых
изменений проверь сценарий в браузере на десктопе и телефоне. Результаты CI
сверяй с точным коммитом PR. Перед выпуском проверь резервную копию, план
отката и целевой контур по runbook. Для финальных образов используй
`bash scripts/checks/check-runtime-images.sh API_IMAGE BOOTSTRAP_IMAGE WEB_IMAGE`.
