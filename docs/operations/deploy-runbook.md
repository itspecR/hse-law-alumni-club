# Эксплуатация Клуба на Ubuntu

> Этот runbook сохраняет обслуживание существующего PostgreSQL/Caddy-контура.
> Новые установки MariaDB/nginx используют [отдельную инструкцию](mariadb-migration.md)
> и `compose.env`. Установщик распознаёт прежнюю конфигурацию версии 1.

Рабочий стек: PostgreSQL → миграции → нативный bootstrap → Django API, web и Caddy.
Отдельного сервера Directus нет. Сохранены совместимые имена таблиц пользователей,
ролей и файлов, а также том `directus_uploads`. Основание – [ADR](../decisions/cms-options.md).

Этот документ задаёт порядок операций. Проверенные SHA, результаты локального live,
Ubuntu и открытые вопросы находятся только в [project-state.md](project-state.md).
Успешный live на Mac не подтверждает выпуск на Ubuntu или публичном сервере.

После настройки целевого окружения подготовьте
[состав компонентов и материалы анализа защищённости через ЕЛК](security-assessment.md).
Отчёты должны относиться к установленным образам; версии только из checkout
не подтверждают состав работающего сервера.

## Пути и доступ

| Путь | Назначение | Права |
|---|---|---|
| `/opt/club` | Чистый checkout выбранного SHA | root, без рабочих секретов |
| `/etc/club/runtime.env` | Конфигурация приложения | root:root, `0600`; каталог `0700` |
| `/etc/club/operations.env` | Пути и пороги systemd | root:root, `0600` |
| `/var/backups/club` | Зашифрованные снимки | Каталог `0700`, файлы `0600` |
| `/var/lib/club-ops` | Lock и последние результаты операций | Закрытый каталог root |
| Docker volumes | `pgdata`, `directus_uploads`, `caddy_data`, `caddy_config` | Имена получают префикс Compose-проекта |

Docker socket даёт полномочия root. Операции ниже выполняются через `sudo`, API и
bootstrap в контейнерах работают как `club` (UID 1000). Полный env, `docker inspect` и вывод
`compose config` не публикуются. [Справочник настроек](../development/configuration.md) различает
секреты, сборку, запуск и операторские команды.

Скрипты эксплуатации принимают Ubuntu 24.04. ARM64 и AMD64 требуют проверки своих
собранных образов; ресурсы выбираются по [capacity.md](capacity.md). Python устанавливается внутри образов. Для тестов вне Docker нужны Python 3.14,
uv и браузер Playwright; Node/pnpm не требуются.

## Подготовка и первый запуск

Для новой установки есть мастер `sudo ./scripts/install.sh` из чистого `/opt/club`.
Он ставит зависимости, создаёт внешнюю конфигурацию и запускает тот же `deploy.sh`.
Локальный режим использует [compose.local.yml](../../deploy/compose.local.yml):
loopback, internal-сеть, Mailpit, production-проверки и внутренняя CA.
Публичный режим запрашивает домены и рабочий SMTP. Пошаговый ввод и доступ из
браузера – в [README](../../README.md#простая-установка).
`/etc/club/operations.env` создаётся с путями выбранного контура; systemd-таймеры
по-прежнему включаются отдельно после проверки первой резервной копии.
Повторный установщик сохраняет конфигурацию; смена версии выполняется после
проверки CI нового SHA. Не используйте мастер для автоматического присоединения
прежних томов или переноса установки Directus.

Ниже – ручной вариант для уже подготовленного env.

Команды рассчитаны на новый `/opt/club`. Существующий checkout сначала проверяют,
его изменения не сбрасывают. Замените `REVIEWED_COMMIT` проверенным полным SHA:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends git ca-certificates
sudo git clone https://github.com/Bogolubov-creator/hse-law-alumni-club.git /opt/club
sudo git -C /opt/club checkout --detach REVIEWED_COMMIT
cd /opt/club
sudo bash scripts/setup-ubuntu.sh
sudo install -d -m 0700 /etc/club
sudo install -m 0600 .env.example /etc/club/runtime.env
sudoedit /etc/club/runtime.env
```

`setup-ubuntu.sh` ставит системные утилиты и при необходимости Docker Engine,
Buildx и Compose из официального APT-источника. Если Docker уже установлен
пакетом Ubuntu `docker.io`, скрипт дополняет его пакетами Ubuntu `docker-buildx`
и `docker-compose-v2` без замены Engine и перезапуска чужих контейнеров.
`podman-docker` или отличный существующий Docker source требует разбора;
скрипт не удаляет их автоматически. Уже установленные пакеты не обновляются
(`--no-upgrade`), а `needrestart` только перечисляет службы
(`NEEDRESTART_MODE=l`), поэтому соседние сайты не перезапускаются. Если
MariaDB уже установлена, установщик её не переустанавливает и не обновляет.
Проверка: `sudo systemctl is-active docker`, `sudo docker compose version`.
Источник процедуры – [Docker для Ubuntu](https://docs.docker.com/engine/install/ubuntu/).

До продолжения заполните env:

- `APP_ENV=production`, `SEED_DEMO=false`.
- Независимые случайные `POSTGRES_PASSWORD`, `CHECKOUT_DB_PASSWORD`,
  `AUTH_SECRET`, `ADMIN_AUTH_SECRET`, `ADMIN_PASSWORD`, `BACKUP_ENCRYPTION_KEY`.
  Для ключа копий нужны ровно 64 hex-символа; храните его отдельную защищённую копию.
- `ADMIN_EMAIL` – начальный администратор; SMTP, отправитель и выбранный канал офиса.
- `PUBLIC_URL` – корневой HTTPS-адрес; `WEB_DOMAIN` – сайт. `ADMIN_DOMAIN` обслуживает
  прежние `/assets` и перенаправляет в `/admin`, Studio на нём больше нет.
- Неиспользуемые ЮKassa, Telegram, push, Sentry и `POINTS_SERVICE_TOKEN` оставьте пустыми.

`CHECKOUT_DATABASE_URL` можно оставить пустым: Compose соберёт URL отдельной роли
из `CHECKOUT_DB_*`. Preflight не допускает подмену `postgres:5432`, внешнюю БД,
PG service/host overrides и подключение API от владельца базы. Порты API/PG на
host запрещены. Для локальной репетиции сначала примените [QA-конфигурацию](ubuntu-vm-rehearsal.md#изолированная-конфигурация).

```bash
sudo env ENV_FILE=/etc/club/runtime.env bash scripts/preflight.sh
sudo env ENV_FILE=/etc/club/runtime.env bash scripts/deploy.sh --current
```

Для QA к **каждой** операции добавляются одинаковые `DEPLOY_COMPOSE_OVERRIDE` и
`HTTPS_CA_FILE`, как в инструкции репетиции. `ENV_FILE` должен быть абсолютным путём
вне checkout, принадлежать оператору команды и иметь права `0600`. Скрипты читают
его как данные, не как shell-код. Окружение процесса имеет приоритет над файлом.

Preflight проверяет ОС, инструменты, чистоту checkout, env, свободное место,
конфигурацию и занятые порты. Минимум свободного места для сборки – 5120 MiB,
настройка `MIN_FREE_DISK_MB`; копия и restore требуют дополнительного запаса.
В проекте с любым контейнером сервиса `directus`, в том числе остановленным,
нативный deploy отклоняется. Используйте [перенос legacy](#перенос-установки-directus).

## Порядок выпуска и права

`deploy.sh` берёт общий `flock`, сохраняет ID образов, собирает выбранный SHA и
проверяет production-конфигурацию API. Если PostgreSQL уже работает, он делает
снимок текущей версии и удерживает API остановленным на время обновления. Затем:

1. Запускает PostgreSQL и, только в QA override, Mailpit.
2. Выполняет `migrate`: SQL-файлы по порядку имён, индексы, `runtime-role.sql`.
3. Выполняет bootstrap отсутствующих ролей, администратора, справочников и home.
4. Запускает API/web/Caddy, перечитывает Caddyfile, проверяет HTTPS `/api/ready` и HTML `/`.

На первой пустой установке снимок отсутствует. До повторного выпуска должны быть
созданы PostgreSQL, API и web; неполный старт требует диагностики. Bootstrap не
сбрасывает пароли, роли, UUID, настройки и редакторский контент. Дубликаты
`alumni.user_id`, неоднозначные роли или неожиданная роль/status начального
администратора останавливают подготовку без автоматического удаления данных.

| Учётная запись | Полномочия |
|---|---|
| Владелец PostgreSQL | Только migrate/bootstrap и операторские команды |
| `club_api` | Данные приложения/файлы, чтение ролей, перечисленные auth-поля пользователей; без superuser/создания БД/ролей/репликации |
| Общий data adapter | Только allowlist таблиц/полей; без хешей, token/TFA и системных изменений ролей |
| Специализированный auth | Чтение хеша/provider/TFA, проверка пароля; изменение пароля и сессий по серверным правилам |

Runtime не читает `club_settings`, прочие CMS metadata и прежний token; не обновляет
роль пользователя. SQL-права столбцов не изолируют строки между участниками – это
задача guards API. Подробности: [security.md](../development/security.md#нативный-auth-sql-права-и-bootstrap).

Существующие SSO/MFA-записи сохраняются, но не переводятся автоматически на вход
одним паролем. Изменение `ADMIN_PASSWORD` в env не меняет пароль созданного аккаунта.
Миграция помечает текущие UUID-аватары `club_upload_kind=avatar`, сохраняя остальные
ключи metadata. Метка остаётся после замены аватара. Необъектные metadata у такого
файла останавливают миграцию до изменений и требуют разбора в изолированной копии.

Для ручного повторения схемы используется тот же построенный сервис `migrate`:

```bash
cd /opt/club
sudo env ENV_FILE=/etc/club/runtime.env bash scripts/apply-indexes.sh
```

Обёртка требует внешний env, общий lock и native-проект; она применяет все миграции,
индексы и runtime-права. Это не команда только для одного индекса. Для обновления
работающего приложения используйте deploy, который останавливает запись на время схемы.
В QA добавьте тот же `DEPLOY_COMPOSE_OVERRIDE`.

## Сотрудники и восстановление доступа

Публичный forgot/reset обслуживает alumni. Сотрудника создаёт или восстанавливает
оператор через `manage-staff`. Команда подключается от владельца БД; пароль
передаётся через stdin либо `STAFF_PASSWORD`, не через аргументы командной строки.

Пример для нового редактора; сначала в закрытом файле сохраните одну строку пароля
длиной 12–100 символов. Для QA добавьте свой `-f /etc/club/qa-compose.yml`:

```bash
sudo install -m 0600 /dev/null /etc/club/staff-password
sudoedit /etc/club/staff-password
sudo sh -c 'docker compose --env-file /etc/club/runtime.env -f /opt/club/docker-compose.yml \
  run --rm --no-deps -T -e STAFF_ACTION=create -e STAFF_ROLE=editor \
  -e STAFF_EMAIL=editor@example.com bootstrap python -m club_ops.cli manage-staff \
  < /etc/club/staff-password'
sudo rm /etc/club/staff-password
```

Для сброса задайте `STAFF_ACTION=reset-password`, ожидаемую роль и существующий
адрес. Повторное create не перезаписывает аккаунт. Reset не повышает alumni, не
снимает suspended, MFA или внешний provider; меняет хеш и поколение сессии одним
COMMIT. Проверьте новый вход и отказ старого JWT, затем удалите временный файл пароля.

## Обновление и отказ операции

Из `/opt/club` после проверки CI и миграций нужного SHA:

```bash
sudo git status --short
sudo git fetch origin
sudo git checkout --detach REVIEWED_COMMIT
sudo env ENV_FILE=/etc/club/runtime.env bash scripts/deploy.sh --current
```

Текущая работающая версия фиксируется в OCI labels API/web и `last-deploy.json`.
Checkout с правками отклоняется. Образы не удаляются автоматически. Старый образ
без revision label требует `ADOPT_DEPLOYED_REVISION` – подтверждённого SHA прежнего
выпуска, а не нового checkout. При наличии labels API и web должны совпадать.

Ошибка даёт ненулевой exit status. Ошибки после preflight записываются в
`last-deploy-attempt.json`; `last-deploy.json` обновляется только при успехе.
Ошибка самого preflight происходит до установки этой записи. После сбоя миграций
API может оставаться остановленным. Проверьте `compose ps`, логи и версию схемы;
не запускайте прежний код без подтверждения совместимости и не удаляйте тома.
Автоматического отката БД нет. Успешный smoke не заменяет пользовательские сценарии.

## Согласованная резервная копия

Для native-проекта нужны работающий PostgreSQL и созданные контейнеры API/web:

```bash
cd /opt/club
sudo env ENV_FILE=/etc/club/runtime.env bash scripts/backup.sh
sudo cat /var/lib/club-ops/last-backup.json
```

Backup удерживает общий lock, останавливает работающий API, проверяет место,
снимает custom dump, uploads и счётчики. После чтения он запускает прежний контейнер
API через `docker start`, без повторного bootstrap, затем до 60 секунд ждёт healthcheck;
при ошибке возврат пытается выполнить trap. Утилиты файлов запускаются по OCI ID
доступного образа PostgreSQL; container config digest не используется как `docker run` image.
Во время копии не запускайте другие
процессы записи, импорты и `manage-staff`: штатный lock не управляет внешними командами.

Набор `snapshot-<UTC>-<pid>` содержит зашифрованный `snapshot.tar.gz.enc`,
`metadata.json`, `SHA256SUMS`. Внутри – dump без ACL/owners, архив uploads, counts,
рабочий `commit.txt`, инструментальный `tool-commit.txt`, состояние checkout,
список миграций/образов, время и внутренние checksum. Рабочий env не включается.

Проверяются запас диска и читаемость полного зашифрованного потока. Завершённый
набор публикуется rename. AES-256-CBC/PBKDF2 шифрует хранение; SHA-256 обнаруживает
повреждение, но не подтверждает автора архива. Используйте доверенные копии и
храните ключ отдельно. Проверка архива не равна восстановлению приложения.

`BACKUP_KEEP_DAYS` по умолчанию 14: старые завершённые локальные наборы удаляются
только после успешной новой копии и offsite-проверки, если она настроена.
`BACKUP_OFFSITE_REMOTE` включает rclone copy и `check --one-way --download`.
Неуспех не обновляет последнюю успешную запись и не запускает retention; завершённая
локальная копия при этом может уже существовать. Пустой remote означает только
локальное хранение. Сроки offsite и тест чтения назначает оператор.

Старые раздельные `.sql.gz.enc` и uploads-копии не являются входом нового restore.
Используйте одну полную копию через `scripts/backup.sh` и `club-backup.timer`;
прежние отдельные задания копирования удалите после проверки нового снимка.

## Изолированное восстановление

`restore.sh` всегда создаёт новый проект `club-restore-*` и новые тома. Он не
перезаписывает исходную БД. Возможны два режима:

| Режим | Код | Действие со схемой |
|---|---|---|
| `exact` – по умолчанию | Чистый native-checkout с SHA из snapshot `commit.txt` | Восстановление dump/files и runtime SQL-роли; без миграций/bootstrap |
| `migrate-legacy` | Чистый native-checkout выбранной версии; отдельный `LEGACY_APP_REVISION` совпадает со snapshot | Восстановление legacy-копии, затем нативные миграции и bootstrap |

Подготовьте `/etc/club/restore.env` и `/etc/club/restore-compose.yml` по
[изолированной QA-конфигурации](ubuntu-vm-rehearsal.md#изолированная-конфигурация),
выбрав свободные loopback-порты, например 10443/10444/10125. Env содержит новый пароль
SQL-runtime, ключ снимка и подходящие сохранённым аккаунтам настройки. Для
migrate-legacy `ADMIN_EMAIL` должен указывать на активного администратора копии;
bootstrap не изменит его пароль. Не добавляйте demo и внешние ключи.

Restore проверяет до запуска:

- Проект и все целевые тома ещё не существуют; только новые named volumes с его
  префиксом, без external/driver_opts, без bind mounts данных API/PG.
- Default-сеть internal; API/migrate/bootstrap/PG используют только её. Нет внешних
  сетей, links, network_mode, extra_hosts или volumes_from в Compose.
- API/PG не публикуют порты; все опубликованные порты остальных сервисов – 127.0.0.1.
- Jobs/demo выключены; SMTP заканчивается в Mailpit; Telegram/оплата/push/Sentry
  выключены; PUBLIC_URL указывает на localhost или 127.0.0.1.
- SQL/PG-настройки указывают только на PostgreSQL этого проекта под ожидаемыми ролями.

Создайте чистый checkout целевого SHA, например `/opt/club-restore-code`. Прочитайте
`application_revision` в доверенном `metadata.json`, выберите точный snapshot:

```bash
cd /opt/club
sudo env ENV_FILE=/etc/club/restore.env \
  DEPLOY_COMPOSE_OVERRIDE=/etc/club/restore-compose.yml \
  RESTORE_CODE_DIR=/opt/club-restore-code \
  RESTORE_PROJECT=club-restore-drill \
  RESTORE_MODE=exact \
  SNAPSHOT_DIR=/var/backups/club/snapshot-YYYYMMDDTHHMMSSZ-PID \
  bash scripts/restore.sh
```

Скрипт проверяет внешний и внутренний checksum, имена/тип tar entries, размеры,
запас диска, SHA и чистоту checkout инструмента снимка/целевого кода. Восстанавливает
dump и файлы, назначает файлам UID/GID 1000, сравнивает counts alumni/orders/ledger/files.
В legacy-режиме это сравнение выполняется **до** миграций; после них нужны отдельные
сверки сохранения UUID, хешей, настроек и контента. Это не автоматическая проверка
каждого файла или всех бизнес-сумм.

В конце стартуют Mailpit/API/web/Caddy. Restore не проверяет HTTPS и не экспортирует
новую CA автоматически. Экспортируйте публичный корень **нового** проекта и проверьте
`/api/ready`, вход, роли, данные и медиа по инструкции репетиции. Повторите чтение
после рестарта. Исходная CA не подходит к новому тому Caddy.

Восстановленный проект остаётся для диагностики, включая неудачный запуск. Повтор
требует другого нового имени. Переключение пользователей и удаление прежних томов
в эти команды не входят. Если после snapshot появились новые записи, возврат к нему
теряет эти изменения; нужен согласованный перенос разницы или окно остановки записи.

## Перенос установки Directus

Нативные `deploy.sh` и `backup.sh` намеренно отклоняют проект с контейнером Directus.
Не обходите проверку удалением контейнера при сохранённой общей БД. Для старой
установки сначала создаётся единый snapshot закреплённым pre-native инструментом:
`17053fad1b61f7408cc21788035b3b52c5da94d4`. Его backup останавливает API и CMS,
копирует БД и `/directus/uploads`, затем запускает прежние контейнеры через
`docker start`, без повторного выполнения зависимостей bootstrap.

Из существующего клона создайте отдельный чистый worktree; исходный checkout и
Compose-проект не переключаются. В env/override указываются настройки **источника**:

```bash
sudo git -C /opt/club worktree add --detach /opt/club-legacy-tool \
  17053fad1b61f7408cc21788035b3b52c5da94d4
cd /opt/club-legacy-tool
sudo docker compose --env-file /etc/club/legacy.env \
  -f docker-compose.yml -f /etc/club/legacy-compose.yml ps -a
sudo env ENV_FILE=/etc/club/legacy.env \
  DEPLOY_COMPOSE_OVERRIDE=/etc/club/legacy-compose.yml \
  ADOPT_DEPLOYED_REVISION=VERIFIED_LEGACY_40_CHAR_SHA \
  STATE_DIR=/var/lib/club-legacy-ops \
  bash scripts/backup.sh
```

Перед копией подтвердите имя source-проекта, его контейнеры, тома и работающий SHA.
`ADOPT_DEPLOYED_REVISION` нужен только без labels; он не устанавливает происхождение
образа сам по себе. Для исходной синтетической VM `club-ubuntu-qa` известен
`93fc86f7b76477d06a404cc47af549695f7a6bdb`; значение неприменимо к другому серверу без
проверки. Не используйте SHA новой native-версии вместо версии источника.

Затем из текущего native-checkout выполните новый изолированный restore:

```bash
cd /opt/club
sudo env ENV_FILE=/etc/club/restore.env \
  DEPLOY_COMPOSE_OVERRIDE=/etc/club/restore-compose.yml \
  RESTORE_CODE_DIR=/opt/club-restore-code \
  RESTORE_PROJECT=club-restore-native \
  RESTORE_MODE=migrate-legacy \
  LEGACY_APP_REVISION=VERIFIED_LEGACY_40_CHAR_SHA \
  SNAPSHOT_DIR=/var/backups/club/snapshot-YYYYMMDDTHHMMSSZ-PID \
  bash scripts/restore.sh
```

`LEGACY_APP_REVISION` должен совпадать с `commit.txt`, а целевой код содержать
`001_native_base.sql`. Пользовательские UUID/хеши, ссылки файлов и старые настройки
не сбрасываются. CMS metadata остаётся в копии; полный JSON настроек архивируется
приватно в `club_settings`. До переключения пройдите отрицательные проверки ролей,
сверьте файлы/хеши/данные и проверьте новый snapshot с последующим exact-restore.
Прежний проект продолжает существовать; дублировать его фоновые интеграции нельзя.

## Таймеры и мониторинг

Systemd-unit рассчитаны на `/opt/club` и `/etc/club/runtime.env`. Уберите только
старые задания Клуба для тех же операций, сохраняя остальные задания сервера:

```bash
cd /opt/club
sudo install -m 0644 deploy/systemd/club-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now club-backup.timer club-monitor.timer
sudo systemctl list-timers club-backup.timer club-monitor.timer
```

Backup: ежедневно 03:30 `Europe/Moscow`, `Persistent=true`; monitor: через пять
минут после загрузки, затем каждые пять минут. Повтор неудачной копии – ручной запуск
`club-backup.service` после исправления причины. Общий lock исключает одновременные
backup/deploy/restore с тем же STATE_DIR.

### Автовыкладка main

`club-autodeploy.timer` раз в минуту запускает `scripts/lib/autodeploy.py`. Скрипт
сравнивает выкаченный коммит с последним коммитом `main` в `origin` (`git ls-remote`).
Новый коммит выкатывается через `scripts/deploy.sh` только после того, как в GitHub
зелёные все проверки CI этого коммита: `build-test`, `mariadb-integration`,
`postgres-integration`, `live-stack`, `secrets`. Выкатывается ровно проверенный SHA
(`DEPLOY_COMMIT`), даже если за это время в `main` появился следующий.

- CI ещё идёт – ждём; CI красный – не выкатываем.
- Идёт копия или другая операция с общим lock – пропускаем минуту, попытка не тратится.
- Ошибка выкладки: `deploy.sh` возвращает прошлую версию, автовыкладка повторяет тот же
  коммит не больше 3 раз с паузой 5 минут, затем ждёт следующий коммит в `main`.
  Счётчик хранится в `STATE_DIR/autodeploy.json`.

Для публичного репозитория токен не нужен (лимит GitHub без токена – 60 запросов
в час, CI спрашивается только при новом коммите). Для приватного создайте токен
только на чтение и положите его в закрытый файл:

```bash
sudo install -m 0600 /dev/null /etc/club/autodeploy.env
sudo editor /etc/club/autodeploy.env   # строка GITHUB_TOKEN=...
```

Включение, проверка и выключение:

```bash
cd /opt/club
sudo install -m 0644 deploy/systemd/club-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now club-autodeploy.timer
sudo journalctl -u club-autodeploy -n 50
sudo systemctl disable --now club-autodeploy.timer
```

В `/etc/club/operations.env` задайте нужные ENV_FILE/override/CA и пороги как
`NAME=value`. Для внутренней CA задают `MONITOR_CERT_MIN_DAYS=0.25`; для публичного
сертификата штатный порог 14 дней. Файл читается systemd, shell-команды в нём запрещены.

```bash
sudo systemctl start club-monitor.service
sudo journalctl -u club-backup.service -u club-monitor.service --since today
sudo cat /var/lib/club-ops/last-monitor.json
```

Monitor проверяет четыре постоянных сервиса – postgres/api/web/caddy, healthy,
OOM/restarts, HTTPS, диск 85%, RAM 90%, возраст копии 36 часов, TLS, ошибки API и
результаты включённых jobs. Первая проверка до первой копии сообщает её отсутствие.
Результат – exit status, journald и `last-monitor.json`. Внешний канал нужно отдельно
выбрать и проверить; локальный monitor не обнаруживает собственную полную остановку.
Docker logs ограничены тремя файлами по 10 MB на сервис; retention journald задаёт оператор.

## Проверка и диагностика

После успешного скрипта проверьте реальные сценарии: регистрация и Mailpit/SMTP,
подтверждение и вход, профиль/верификация, заявка, роли офиса, загрузка/открытие файла,
сохранение после рестарта. Обычные unit и mocked E2E не подтверждают запись в БД.
Команды SQL, live и браузерных наборов: [testing.md](../development/testing.md).

| Симптом | Проверка и действие |
|---|---|
| Preflight: найден Directus | Использовать isolated migrate-legacy; не удалять контейнер для обхода |
| Занят порт | `sudo ss -ltnp`, выбрать свободный loopback-порт QA или устранить конфликт |
| Bootstrap: роль/status или duplicate user_id | Проверить записи в изолированной копии; не сбрасывать пароль/роль и не удалять дубликаты автоматически |
| `/api/ready` даёт 503 | Проверить PostgreSQL, migrate, bootstrap и SQL grants, затем вход |
| Сайт 502 при ready 200 | Проверить web/Caddy, сборку и маршрут |
| TLS не проходит | Проверить имя, часы, CA/DNS/ACME; не использовать `-k` для признания успеха |
| Копия не создаётся | Проверить lock, место, labels/SHA, ключ и journal; повторить после исправления |
| Restore отклоняет том/сеть | Выбрать новый project и новые тома, проверить эффективный override |
| Старый пароль сотрудника | Использовать manage-staff; изменение ADMIN_PASSWORD в env не является reset |

При инциденте сохраните SHA, image ID и очищенные журналы. Возврат кода допустим
только при совместимой схеме; иначе используйте проверенную копию в новом проекте.
Ротация JWT-ключей отзывает соответствующие сессии. Ротация пароля SQL требует
согласованного изменения роли и env. Публичные DNS/TLS, почта, магазин и внешние
сообщения проверяются отдельно после решения владельца.
