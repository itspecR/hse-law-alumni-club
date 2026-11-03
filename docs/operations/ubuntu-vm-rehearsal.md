# Репетиция native-выпуска в Ubuntu на Mac

Это историческая репетиция предыдущего стека. Для текущего Django-приложения
проверки установки выполняются отдельно в CI; [порядок обновления](deploy-runbook.md).

Инструкция проверяет выбранный SHA в отдельном локальном контуре Ubuntu 24.04:
PostgreSQL, migrate, native bootstrap, FastAPI, web, Caddy и Mailpit. Реальные
участники, почтовые учётные данные и ключи внешних интеграций здесь не используются.

Native live на локальном Docker прошёл запись desktop/mobile, повторный bootstrap
и чтение после рестарта. Это не проверка Ubuntu. Результат VM-репетиции и точный SHA
фиксируются в [project-state.md](project-state.md); инструкция не объявляет её завершённой.
Общий порядок операций и перенос старой CMS – в [runbook](deploy-runbook.md).

## Выбор существующей или новой VM

На Mac проверьте имеющиеся VM и свободное место:

```bash
limactl list
df -h ~
```

Исходная VM `club-ubuntu-qa` содержит исторический синтетический Directus-стенд.
Не удаляйте и не переименовывайте его тома ради native-запуска. В этой VM можно
создать новый Compose-проект с другими портами либо использовать новую VM.
Имена проектов, конфиги и снимки источника должны оставаться различимыми.

Для новой VM нужен [Lima](https://lima-vm.io/docs/installation/). Если он уже
установлен, повторная установка не нужна. Команда создания ниже применяется
только к ещё не существующему имени `club-native-ubuntu`:

```bash
brew install lima
limactl start --yes --name=club-native-ubuntu --vm-type=vz \
  --cpus=4 --memory=6 --disk=20 --mount-none template:ubuntu-24.04
limactl shell club-native-ubuntu
```

Пример выделяет 4 vCPU, 6 GiB RAM и 20 GiB виртуального диска, без монтирования
домашнего каталога Mac. Это параметры исходного размера репетиции, а не гарантия
места для двух стеков, сборки и всех копий. До запуска сверьте [capacity.md](capacity.md),
`df -h` внутри VM и на Mac; preflight требует ещё минимум 5 GiB свободного места.
Существующую VM запускают по имени без повторного создания.

Перед новой сборкой оставляйте на Mac не менее 10 GiB свободного места сверх
существующего образа VM и проверяйте его во время скачивания и сборки. Это запас
данной репетиции, не измеренный минимальный предел. Виртуальный диск растёт по
мере записи; проверка свободного места внутри Ubuntu не обнаруживает заполнение
физического диска Mac. Ошибка записи прерывает deploy до обновления сервисов;
после неё сначала проверяют хост и файловую систему VM, затем повторяют запуск.

У VM собственный Docker Engine. Docker Desktop на Mac его не заменяет. Все команды
следующих разделов, кроме явно отмеченных, выполняются **в Ubuntu**.

## Код и зависимости

Не выбирайте автоматически движущуюся `main`. Замените `REVIEWED_COMMIT` точным
проверенным native-SHA. Для нового `/opt/club`:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends git ca-certificates
sudo git clone https://github.com/Bogolubov-creator/hse-law-alumni-club.git /opt/club
sudo git -C /opt/club checkout --detach REVIEWED_COMMIT
cd /opt/club
sudo bash scripts/setup-ubuntu.sh
sudo docker version
sudo docker compose version
sudo docker buildx version
sudo systemctl is-active docker
```

На существующей VM используйте отдельный чистый checkout нужного SHA, сохраняя
исходный. Для переноса данных [legacy snapshot](deploy-runbook.md#перенос-установки-directus)
снимается прежним закреплённым инструментом, а не этим native-deploy.
Для запуска тестов вне Docker устанавливаются
версии из [testing.md](../development/testing.md) отдельно.

## Изолированная конфигурация

Создайте внешний env, замените все нужные секреты независимыми случайными значениями,
например отдельными результатами `openssl rand -hex 32`. Рабочие ключи не копируются.
Если `/etc/club/runtime.env` уже относится к другому контуру, используйте новое имя,
например `/etc/club/native-runtime.env`, и замените путь во всех командах ниже.
Не перезаписывайте существующий env.

```bash
sudo install -d -m 0700 /etc/club
sudo install -m 0600 /opt/club/.env.example /etc/club/runtime.env
sudoedit /etc/club/runtime.env
```

Нужны POSTGRES/CHECKOUT пароли, пароль
начального администратора, два JWT-секрета и ключ резервной копии. `CHECKOUT_DATABASE_URL`
оставьте пустым, чтобы Compose собрал локальный URL отдельной роли.

Помимо секретов задайте следующие значения; это локальные назначения Mailpit:

```dotenv
APP_ENV=production
SEED_DEMO=false
JOBS_ENABLED=false
DPO_SYNC_ENABLED=false
NEWS_SYNC_ENABLED=false
ADMIN_EMAIL=qa-admin@example.com
PUBLIC_URL=https://localhost:9445
WEB_DOMAIN=localhost
ADMIN_DOMAIN=admin.localhost
ACME_EMAIL=qa@example.com
OFFICE_NOTIFY_CHANNEL=email
OFFICE_EMAIL=qa-office@example.com
SMTP_HOST=mailpit
SMTP_PORT=1025
SMTP_FROM=qa-noreply@example.com
SMTP_USER=
SMTP_PASS=
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_POLLING=false
OFFICE_TG_BOT_TOKEN=
OFFICE_TG_CHAT_ID=
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
SENTRY_DSN=
SUPPORT_ENABLED=false
BACKUP_OFFSITE_REMOTE=
```

Сохраните override в `/etc/club/qa-compose.yml`, root:root, `0600`:

```yaml
name: club-native-ubuntu
services:
  caddy:
    networks: [default, browser]
    ports: !override
      - "127.0.0.1:9445:443"
      - "127.0.0.1:9446:443"
  mailpit:
    image: axllent/mailpit:v1.31.5@sha256:94f6b70c1436defae6799d9ac7ff0eeff9540465b842026d5cdb018f1c0234ea
    networks: [default, browser]
    ports:
      - "127.0.0.1:9126:8025"
    security_opt: ["no-new-privileges:true"]
    mem_limit: 128m
    logging:
      driver: json-file
      options: { max-size: "5m", max-file: "2" }
networks:
  default:
    internal: true
  browser:
```

API, PostgreSQL, migrate и bootstrap находятся только в internal-сети. Caddy и
Mailpit дополнительно подключены к bridge для браузерного доступа; опубликованные
порты слушают только loopback. `9446` – прежний admin-домен с redirect и legacy-assets,
а не Studio. API/PG портов host не имеют. Docker build скачивает зависимости до
запуска, изоляция runtime не отменяет сетевые обращения самой сборки.

Если 9445/9446/9126 заняты, выберите другие порты и согласованно измените PUBLIC_URL,
override и команды. Для restore используйте новые, например 10443/10444/10125;
тот же override без существующих volumes удовлетворяет требованиям restore guard.
`-p club-restore-*` в restore переопределяет имя из YAML.

## Первый выпуск и HTTPS

Из `/opt/club`:

```bash
sudo docker compose --env-file /etc/club/runtime.env \
  -f docker-compose.yml -f /etc/club/qa-compose.yml config --quiet
sudo env ENV_FILE=/etc/club/runtime.env \
  DEPLOY_COMPOSE_OVERRIDE=/etc/club/qa-compose.yml \
  HTTPS_CA_FILE=/etc/club/local-ca.crt bash scripts/preflight.sh
sudo env ENV_FILE=/etc/club/runtime.env \
  DEPLOY_COMPOSE_OVERRIDE=/etc/club/qa-compose.yml \
  HTTPS_CA_FILE=/etc/club/local-ca.crt bash scripts/deploy.sh
```

Deploy требует production-конфигурацию, но SMTP здесь заканчивается в Mailpit.
На первом localhost HTTPS-запуске отсутствующий `HTTPS_CA_FILE` экспортируется из
Caddy. Это публичный корень новой локальной CA, не закрытый ключ; глобальное
доверие ОС команда не меняет. Если файл уже существует от другого проекта,
не подменяйте ошибку TLS отключением проверки: получите корень нужного нового Caddy.

```bash
sudo curl --fail --cacert /etc/club/local-ca.crt https://localhost:9445/api/ready
sudo curl --fail --cacert /etc/club/local-ca.crt -I https://localhost:9445/
sudo curl --fail --cacert /etc/club/local-ca.crt \
  --resolve admin.localhost:9446:127.0.0.1 -I https://admin.localhost:9446/
sudo docker compose --env-file /etc/club/runtime.env \
  -f docker-compose.yml -f /etc/club/qa-compose.yml ps -a
```

Ожидаются readiness `status: ok`, HTML сайта, redirect admin-домена в `/admin`,
healthy у четырёх постоянных сервисов. У одноразовых migrate/bootstrap успешное
завершение отличается от состояния running. `curl -k` не подтверждает TLS.
В этом override HTTP-порт не опубликован; публичный HTTP→HTTPS redirect проверяется
отдельно в контуре, где доступен порт 80.

Для обращения с Mac скопируйте только публичный CA. Сначала в Ubuntu:

```bash
sudo install -m 0644 /etc/club/local-ca.crt /tmp/club-native-qa-root.crt
```

Затем на Mac, подставив фактическое имя VM:

```bash
limactl copy club-native-ubuntu:/tmp/club-native-qa-root.crt /tmp/club-native-qa-root.crt
curl --fail --cacert /tmp/club-native-qa-root.crt https://localhost:9445/api/ready
```

Проверьте перенаправление loopback-порта Lima отдельно; успешный запрос внутри VM
не доказывает доступ с Mac. Если порт занят на Mac, перенастройте проброс, не
открывайте API или PostgreSQL наружу. Браузерные сценарии выполняйте в тестовом
профиле; проверка TLS через доверенный CA остаётся отдельным обязательным шагом.

После guest `reboot` в репетиции приложение отвечало внутри Ubuntu, но hostagent
Lima сохранил закрытое gRPC-соединение: запрос с Mac получал connection reset.
Перезапуск существующей VM восстановил loopback-проброс без удаления томов:

```bash
limactl stop club-native-ubuntu
limactl start club-native-ubuntu
curl --fail --cacert /tmp/club-native-qa-root.crt https://localhost:9445/api/ready
```

Это диагностика Lima на Mac. Не применяйте пересоздание VM или томов для исправления
проброса; сначала проверьте тот же HTTPS внутри Ubuntu.

## Пользовательские сценарии и повторный запуск

В браузере на компьютере и ширине телефона пройдите:

1. Регистрацию синтетического адреса, получение письма в Mailpit
   `http://localhost:9126`, подтверждение и вход. До подтверждения аккаунт unverified.
2. Сброс пароля через новое письмо, вход новым паролем, отказ старого JWT.
3. Верификацию участника администратором, изменение профиля и повторное чтение.
4. Создание контента, корзину и заявку, смену статуса в офисе.
5. Вход editor, отказ чувствительных административных действий.
6. Upload/preview медиа; draft-изображение закрыто, опубликованное доступно, audio
   не открывается общим media-маршрутом, используемый файл не удаляется.
7. Аватар 256×256 и сохранение исходного файла; чтение прежних UUID при legacy-переносе.

Создание editor и reset сотрудника выполняйте [операторской командой](deploy-runbook.md#сотрудники-и-восстановление-доступа).
SQL-права API можно проверить без вывода credentials:

```bash
sudo docker compose --env-file /etc/club/runtime.env \
  -f docker-compose.yml -f /etc/club/qa-compose.yml \
  run --rm --no-deps -T api python - < scripts/tests/test-runtime-permissions.py
```

Перед повторным deploy измените синтетические настройки/контент и сохраните
контрольные значения. Повторите тот же `deploy.sh`: он создаёт согласованный snapshot
и повторяет миграции/bootstrap, сохраняя пароли, UUID, роли и ручной контент.
Deploy не выполняет restore автоматически. После рестарта постоянных сервисов
повторите чтение профиля, заявки, контента и отзывов сессий:

```bash
sudo docker compose --env-file /etc/club/runtime.env \
  -f docker-compose.yml -f /etc/club/qa-compose.yml restart postgres api web caddy mailpit
```

Сценарий `scripts/tests/test-live.sh` создаёт собственный проект `club-ci-live` на HTTP
8180 с Mailpit и сам удаляет его. Он не принимает URL этого HTTPS-стенда. При запуске
внутри Ubuntu это дополнительная проверка собранных сервисов; она не заменяет
сохранение/восстановление именно проверяемого QA-проекта.

## Копия, restore и мониторинг

Снимите полную копию и проверьте её отдельным exact-restore по [runbook](deploy-runbook.md#изолированное-восстановление).
Для источника Directus используйте [migrate-legacy](deploy-runbook.md#перенос-установки-directus).
Каждый restore получает новый `club-restore-*`, новые тома, отдельные env/порты/CA.
Результат не переносится на source-проект автоматически.

После restore Caddy создаёт новую CA. Скрипт restore её не экспортирует; пример
для ранее выбранного `club-restore-drill` и порта 10443:

```bash
sudo docker compose -p club-restore-drill --env-file /etc/club/restore.env \
  -f /opt/club-restore-code/docker-compose.yml -f /etc/club/restore-compose.yml \
  cp caddy:/data/caddy/pki/authorities/local/root.crt /etc/club/restore-root.crt
sudo curl --fail --cacert /etc/club/restore-root.crt https://localhost:10443/api/ready
```

Сравните количество и ключевые значения строк, пользовательские UUID/хеши без
публикации самих хешей, суммы/статусы, checksum uploads и ссылки на файлы. Проверьте
вход, роли, повторное чтение и медиа. Автоматическое сравнение четырёх counts в
restore не заменяет эту сверку, особенно после legacy-миграций.

Таймеры устанавливаются по [runbook](deploy-runbook.md#таймеры-и-мониторинг).
В `/etc/club/operations.env` для этого контура задайте:

```dotenv
ENV_FILE=/etc/club/runtime.env
DEPLOY_COMPOSE_OVERRIDE=/etc/club/qa-compose.yml
HTTPS_CA_FILE=/etc/club/local-ca.crt
MONITOR_CERT_MIN_DAYS=0.25
```

Если использовано другое имя env, поправьте его и здесь. Monitor ожидает успешную
копию, четыре healthy-сервиса и HTTPS; без первой копии его отказ ожидаем.
Внешнего оповещения и offsite в этой репетиции нет.

## Завершение и границы результата

Запишите в единый журнал SHA, команды, exit status, ресурсы VM, checksums, роль,
движок браузера, размеры экрана и оставшиеся ограничения. Секреты, тела писем,
рабочие выгрузки и токены в репозиторий не входят. Скриншот без проверки повторного
чтения не доказывает сохранение. Для write/read-фаз live сохраняются разные каталоги.

Чтобы освободить память Mac без удаления данных:

```bash
limactl stop club-native-ubuntu
```

При следующем продолжении работы:

```bash
limactl start club-native-ubuntu
```
Не удаляйте исходный legacy-проект и его копии до завершения проверки и окна отката.
ARM64-репетиция не подтверждает AMD64, внутренняя CA не проверяет публичный ACME,
Mailpit не подтверждает доставку внешней почты. Оплата, Telegram, push, offsite и
публичный сервер проверяются отдельно после их согласованного подключения.
