# Эксплуатация Клуба на Ubuntu

Сайт работает на MariaDB из пакетов Ubuntu и nginx. MariaDB запущена как служба ОС,
API, web, nginx и операторский образ запускаются через
[compose.mariadb.yml](../../deploy/compose.mariadb.yml). Отдельного сервера Directus
нет: совместимые имена таблиц пользователей, ролей и файлов сохранены, основание –
[ADR](../decisions/cms-options.md).

Документ задает порядок операций. Проверенные SHA и открытые вопросы находятся в
[project-state.md](project-state.md). Состав компонентов для анализа защищенности
готовится по [security-assessment.md](security-assessment.md); отчеты относятся к
установленным образам, а не к версиям из checkout.

## Пути и доступ

| Путь | Назначение | Права |
|---|---|---|
| `/opt/club` | Чистый checkout выбранного SHA | root, без рабочих секретов |
| `/etc/club/compose.env` | Compose env-файл: `CLUB_STACK=mariadb`, база, образы, порты, пути | root, `0600`; каталог `0700` |
| `/etc/club/runtime.env` | Настройки API | root, `0600` |
| `/etc/club/operator.env` | Настройки операторского образа: миграции, сотрудники, письма | root, `0600` |
| `/etc/club/operations.env` | `ENV_FILE` для таймеров systemd | root, `0600` |
| `/etc/club/initial-admin.txt` | Первичные почта и пароль администратора | root, `0600` |
| `/var/lib/club` | `uploads`, каталог переноса `transfer` и сертификат `tls` | uploads и transfer принадлежат UID 1000 |
| `/var/backups/club` | Зашифрованные копии | Закрытый каталог root |
| `/var/lib/club-ops` | Lock и результаты операций: `last-deploy.json`, `last-backup.json` | Закрытый каталог root |

Docker socket дает полномочия root, поэтому операции выполняются через `sudo`.
API и оператор работают в контейнерах как UID 1000, nginx – с UID/GID 101. Полный
env, `docker inspect` и вывод `compose config` не публикуются.
[Справочник настроек](../development/configuration.md) различает секреты, сборку,
запуск и операторские команды.

Скрипты эксплуатации рассчитаны на Ubuntu 24.04. Ресурсы выбираются по
[capacity.md](capacity.md). Python установлен внутри образов; Node и pnpm не нужны.

## Подготовка и первый запуск

Новая установка выполняется мастером `sudo ./scripts/install.sh` из чистого
`/opt/club`. Он ставит Docker и MariaDB, создает базу и отдельные аккаунты
оператора и API, пишет файлы из таблицы выше и запускает `deploy.sh`. Пошаговый
ввод, режимы локальной репетиции, сервера и публичного домена описаны в
[README](../../README.md#простая-установка), домен и купленный сертификат – в
[launch.md](launch.md).

Повторный запуск мастера сохраняет конфигурацию, пароли и данные. Существующий
каталог данных к новой установке автоматически не присоединяется.

## Файлы конфигурации и TLS

Мастер пишет файлы сам; вручную их меняют только по этому разделу. Все файлы
хранятся вне checkout с правами `0600`. У API нет пароля оператора. В файле API
задаются `APP_ENV=production`, `SEED_DEMO=false` и рабочие параметры SMTP и
уведомлений. Подключение API имеет форму
`mariadb://<роль-api>:<пароль>@localhost/<база>?unix_socket=/run/mysqld/mysqld.sock`;
специальные символы имени и пароля кодируются для URL. MariaDB должна принимать TCP
только на loopback, порт 3306 в firewall открывать не нужно.

В файле оператора `DATABASE_URL` и `CHECKOUT_DATABASE_URL` указывают на аккаунт
оператора, а `CHECKOUT_DB_USER`, `CHECKOUT_DB_HOST=localhost` и `CHECKOUT_DB_PASSWORD`
описывают отдельный аккаунт API. Команда `configure-mariadb-role` заменяет права и
пароль этого выделенного аккаунта; общий аккаунт другой системы для нее не подходит.

| Параметр `compose.env` | Значение |
|---|---|
| `CLUB_RUNTIME_ENV`, `CLUB_OPERATOR_ENV` | Абсолютные пути к файлам API и оператора |
| `CLUB_UPLOADS_DIR`, `CLUB_TRANSFER_DIR` | Каталоги файлов сайта и обмена, владелец UID 1000 |
| `CLUB_TLS_DIR` | Каталог `fullchain.pem` и `privkey.pem` |
| `CLUB_API_IMAGE`, `CLUB_WEB_IMAGE`, `CLUB_OPERATOR_IMAGE`, `CLUB_NGINX_IMAGE` | Образы одной ревизии |
| `CLUB_BIND_ADDRESS`, `CLUB_HTTP_PORT`, `CLUB_HTTPS_PORT` | Адрес и порты nginx |
| `CLUB_STATE_DIR`, `CLUB_BACKUP_DIR` | Состояние операций и каталог копий |

nginx собирается через `deploy/nginx.Dockerfile` из закрепленного официального
образа и запускается с UID/GID 101. Каталог TLS должен разрешать чтение этому GID,
ключ – иметь владельца `root:101` и права `0640`. После замены файлов сертификата
пересоздайте nginx. Купленный сертификат запрашивается, проверяется и заменяется
командой `scripts/certificate.sh` ([README](../../README.md#купленный-сертификат)).
nginx заменяет входящие заголовки адреса клиента; правила – в
`deploy/nginx.conf.template` и
[документации nginx](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header).

## Порядок выпуска и права

`scripts/deploy.sh` без аргумента получает `main` из GitHub и выкатывает его;
с именем ветки выкатывает ее; с `--current` выкатывает checkout как есть. Скрипт
вызывает `mariadb_native.py deploy`, который под общим lock выполняет семь шагов:

1. Проверяет конфигурацию: закрытые порты API, web и оператора, `APP_ENV=production`,
   `SEED_DEMO=false`, 5 GiB свободного места Docker, службу MariaDB, доступ к базе
   и срок сертификата.
2. Собирает образы api, web, operator и nginx с `VCS_REF` и проверяет рабочие
   настройки API.
3. Загружает вспомогательные образы до остановки сайта.
4. Если API работает, делает резервную копию.
5. Останавливает запись и применяет миграции, права аккаунта API и начальные данные.
6. Запускает API, web и nginx.
7. Проверяет TLS, сайт и `/api/ready`, записывает `last-deploy.json`.

При ошибке результат попадает в `last-deploy-attempt.json`, запись останавливается,
`last-deploy.json` не меняется. Если выкладка из GitHub не удалась, `deploy.sh`
возвращает прошлую версию checkout и запускает ее; если миграции успели
примениться, перед этим нужно восстановить базу из копии.

| Аккаунт | Полномочия |
|---|---|
| Оператор MariaDB | Миграции, bootstrap, управление сотрудниками, DDL и выдача прав своей базы |
| Аккаунт API | Таблицы и столбцы приложения; без `club_settings` и изменения ролей |
| Общий data adapter | Только allowlist таблиц и полей; без хешей, token и TFA |
| Специализированный auth | Проверка и смена пароля и сессий по серверным правилам |

Пароль оператора API не получает. SQL-права столбцов не изолируют строки между
участниками, это задача проверок API. Bootstrap не сбрасывает пароли, роли, UUID,
настройки и редакторский контент. Подробности –
[security.md](../development/security.md#нативный-auth-sql-права-и-bootstrap).

## Сотрудники и восстановление доступа

Публичный forgot/reset обслуживает выпускников. Сотрудника создает или
восстанавливает оператор командой `manage-staff`. Команда подключается от
оператора базы; пароль передается через stdin, а не через аргументы.

Пример для нового редактора; сначала в закрытом файле сохраните одну строку пароля
длиной 12-100 символов:

```bash
sudo install -m 0600 /dev/null /etc/club/staff-password
sudoedit /etc/club/staff-password
sudo sh -c 'docker compose --env-file /etc/club/compose.env -f /opt/club/deploy/compose.mariadb.yml \
  run --rm --no-deps -T -e STAFF_ACTION=create -e STAFF_ROLE=editor \
  -e STAFF_EMAIL=editor@example.com operator python -m club_ops.cli manage-staff \
  < /etc/club/staff-password'
sudo rm /etc/club/staff-password
```

Для сброса задайте `STAFF_ACTION=reset-password`, ожидаемую роль и существующий
адрес. Повторное create не перезаписывает аккаунт. Reset не повышает выпускника, не
снимает блокировку, MFA или внешний provider; он меняет хеш и поколение сессии одной
транзакцией. Проверьте новый вход и отказ старой сессии. Изменение `ADMIN_PASSWORD`
в env не меняет пароль созданного аккаунта.

### Проверка почты

После настройки SMTP отправьте проверочное письмо на свой адрес. Команда берет
настройки из закрытого файла оператора, пишет, куда и через какой сервер ушло письмо,
а при ошибке называет ее тип без пароля:

```bash
sudo sh -c 'docker compose --env-file /etc/club/compose.env -f /opt/club/deploy/compose.mariadb.yml \
  run --rm --no-deps -T -e TEST_MAIL_TO=owner@example.com operator python -m club_ops.cli send-test-mail'
```

Письмо уходит от имени «Клуб выпускников факультета права НИУ ВШЭ» с адреса `SMTP_FROM`;
если задан `MAIL_REPLY_TO`, ответ на письмо уходит на этот адрес. Проверьте, что
письмо не попало в «Спам»: иначе проверьте записи SPF, DKIM и DMARC домена.

### Код входа в панель

Когда SMTP настроен, вход в панель идет в два шага: почта и пароль, затем код из
письма на почту сотрудника. Код действует 10 минут и срабатывает один раз. Если почта
не работает и письмо не пришло, оператор выдает код командой `admin-code`. Она
подключается от оператора базы, проверяет, что адрес принадлежит активному локальному
сотруднику, и печатает код в терминал; код из письма после этого не действует.
Команде нужен тот же `AUTH_SECRET`, что у API: он есть в закрытом файле оператора.

```bash
sudo sh -c 'docker compose --env-file /etc/club/compose.env -f /opt/club/deploy/compose.mariadb.yml \
  run --rm --no-deps -T -e STAFF_EMAIL=admin@example.com operator python -m club_ops.cli admin-code'
```

Сотрудник сначала вводит почту и пароль, затем этот код. Не пересылайте код в
мессенджерах и почте: сообщите его лично или по телефону.

## Обновление и отказ операции

Обычное обновление – `sudo ./scripts/deploy.sh` из `/opt/club`: выкатывается `main`.
Для проверенного коммита ветки задайте `DEPLOY_COMMIT` с полным SHA; коммит должен
входить в выкатываемую ветку. Локальные правки в `/opt/club` при выкладке из GitHub
стираются.

Ошибка дает ненулевой код завершения. После сбоя миграций API остается остановленным.
Проверьте `docker compose ps`, журналы и `last-deploy-attempt.json`; не запускайте
прежний код без подтверждения совместимости схемы и не удаляйте данные.
Автоматического отката базы нет. Успешная проверка `/api/ready` не заменяет
пользовательские сценарии.

## Согласованная резервная копия

```bash
sudo env ENV_FILE=/etc/club/compose.env bash /opt/club/scripts/backup.sh
sudo cat /var/lib/club-ops/last-backup.json
```

Копия выполняется под общим lock: запись останавливается, `mariadb-dump
--single-transaction --hex-blob` снимает базу, архивируются uploads, сохраняются
контрольные суммы и ревизия. Набор шифруется AES-256-CBC с PBKDF2 ключом
`BACKUP_ENCRYPTION_KEY`; ключ храните отдельно от копии. Копия использует
закрепленный образ оператора действующей версии из `last-deploy.json`, даже если
уже собраны новые образы. После копирования возвращается прежний контейнер API.

`BACKUP_KEEP_DAYS` по умолчанию 14. `BACKUP_OFFSITE_REMOTE` включает копирование
через rclone. Проверка архива не равна восстановлению приложения.

## Изолированное восстановление

Восстановление создает отдельный пустой контур и не трогает рабочую базу:

```bash
sudo env ENV_FILE=/etc/club/compose.env python3 /opt/club/scripts/lib/mariadb_native.py prepare-restore --config-dir /etc/club-restore
sudo env ENV_FILE=/etc/club/compose.env SNAPSHOT_FILE=/var/backups/club/snapshot-YYYYMMDD.tar.gz.enc RESTORE_ENV_FILE=/etc/club-restore/compose.env bash /opt/club/scripts/restore.sh
sudo env ENV_FILE=/etc/club-restore/compose.env bash /opt/club/scripts/deploy.sh --current
```

Нужны checkout ревизии из копии, новая пустая база, отдельный пустой каталог файлов
и новый проект. Проверяются отпечатки всех таблиц и SHA-256 каждого файла.
Внешние действия выключены, письма остаются в Mailpit. По умолчанию HTTPS контура
восстановления – `https://localhost:9543`, его CA – `/etc/club-restore/local-ca.crt`.
Если после копии появились новые записи, возврат к ней теряет эти изменения.

## Таймеры и мониторинг

Systemd-unit рассчитаны на `/opt/club`; файл `/etc/club/operations.env`, созданный
мастером, задает `ENV_FILE=/etc/club/compose.env`.

```bash
cd /opt/club
sudo install -m 0644 deploy/systemd/club-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now club-backup.timer club-monitor.timer
sudo systemctl list-timers club-backup.timer club-monitor.timer
```

Backup: ежедневно в 03:30 `Europe/Moscow`, `Persistent=true`; monitor: через пять
минут после загрузки, затем каждые пять минут. Повтор неудачной копии – ручной запуск
`club-backup.service` после исправления причины. Общий lock исключает одновременные
backup, deploy и restore.

Monitor проверяет TLS и `/api/ready`, срок сертификата, состояние healthy у api, web
и nginx и наличие успешной копии за последние 36 часов. Результат – код завершения
и journald:

```bash
sudo systemctl start club-monitor.service
sudo journalctl -u club-backup.service -u club-monitor.service --since today
```

Локальный monitor не обнаруживает собственную полную остановку; внешний канал
оповещения выбирается и проверяется отдельно.

### Автовыкладка main

`club-autodeploy.timer` раз в минуту запускает `scripts/lib/autodeploy.py`. Скрипт
сравнивает выкаченный коммит с последним коммитом `main` в `origin` (`git ls-remote`).
Новый коммит выкатывается через `scripts/deploy.sh` только после того, как в GitHub
зеленые все проверки CI этого коммита из списка `REQUIRED_CHECKS` в
`scripts/lib/autodeploy.py`. Выкатывается ровно проверенный SHA (`DEPLOY_COMMIT`),
даже если за это время в `main` появился следующий.

- CI еще идет – ждем; CI красный – не выкатываем.
- Идет копия или другая операция с общим lock – пропускаем минуту, попытка не тратится.
- Ошибка выкладки: `deploy.sh` возвращает прошлую версию, автовыкладка повторяет тот же
  коммит не больше 3 раз с паузой 5 минут, затем ждет следующий коммит в `main`.
  Счетчик хранится в `autodeploy.json` каталога состояния.

- GitHub не ответил о CI (лимит запросов, сеть, ошибка API) – в журнале строка
  «GitHub не ответил о CI» с ответом GitHub, попытка не тратится, проверка повторится
  через минуту.

CI спрашивается только у нового коммита, который можно выкатывать сейчас: для уже
выкаченного, на паузе после ошибки, после трех неудачных попыток и во время другой
операции обслуживания запроса к GitHub нет. Для публичного репозитория токен не
нужен (лимит GitHub без токена – 60 запросов в час). Для приватного создайте токен
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

## Проверка и диагностика

После выкладки проверьте реальные сценарии: регистрация и письмо, подтверждение и
вход, профиль и проверка выпускника, заявка, роли панели, загрузка и открытие файла,
сохранение после рестарта. Команды тестов: [testing.md](../development/testing.md).

| Симптом | Проверка и действие |
|---|---|
| Занят порт | `sudo ss -ltnp`, выбрать свободный порт или устранить конфликт |
| Bootstrap: роль, статус или дубликат `user_id` | Проверить записи в изолированной копии; не сбрасывать пароль и роль и не удалять дубликаты автоматически |
| `/api/ready` дает 503 | Проверить службу MariaDB, миграции, права аккаунта API, затем вход |
| Сайт 502 при ready 200 | Проверить web, nginx и сборку |
| TLS не проходит | Проверить имя, часы и сертификат; не использовать `-k` для признания успеха |
| Копия не создается | Проверить lock, место, ключ и journal; повторить после исправления |
| Старый пароль сотрудника | Использовать `manage-staff`; изменение `ADMIN_PASSWORD` в env не сбрасывает пароль |

При инциденте сохраните SHA, ID образов и очищенные журналы. Возврат кода допустим
только при совместимой схеме; иначе используйте проверенную копию в новом контуре.
Ротация JWT-ключей отзывает соответствующие сессии. Ротация пароля SQL требует
согласованного изменения роли и env.
