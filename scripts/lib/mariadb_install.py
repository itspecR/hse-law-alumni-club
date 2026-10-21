import fcntl
import ipaddress
import json
import os
import pathlib
import secrets
import shlex
import shutil
import time
from urllib.parse import urlsplit

from mariadb_common import FILES, HELPERS, REPO, Context, connection_url, create_database, directory, output, run, sql

CONTOURS = {"1": "local", "2": "public", "3": "server"}
LISTENERS = {"local": ("127.0.0.1", 9443, 9080), "public": ("0.0.0.0", 443, 80), "server": ("0.0.0.0", 9443, 9080)}
MAIL_PORT = 8025
APT_LOCKS = (pathlib.Path("/var/lib/dpkg/lock-frontend"), pathlib.Path("/var/lib/apt/lists/lock"))
APT_WAIT_SECONDS = 600
APT_POLL_SECONDS = 5


def contour(value):
    if value not in CONTOURS:
        raise ValueError("Выберите 1, 2 или 3")
    return CONTOURS[value]


def server_address(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return HELPERS["hostname"](value)
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("Нужен адрес, по которому сервер открывается из сети")
    return str(address)


def subject(address):
    try:
        return "IP:" + str(ipaddress.IPv4Address(address))
    except ValueError:
        return "DNS:" + address


def public_url(address, port):
    return "https://" + address + ("" if port == 443 else ":" + str(port))


def busy_ports(ports):
    return [port for port in ports if output(["ss", "-H", "-lnt", "sport = :" + str(port)])]


def apt_busy():
    for path in APT_LOCKS:
        if not path.exists():
            continue
        with path.open("a") as handle:
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.lockf(handle, fcntl.LOCK_UN)
    return False


def wait_for_apt():
    deadline = time.monotonic() + APT_WAIT_SECONDS
    if apt_busy():
        print("Ждём завершения другой установки пакетов, например автоматических обновлений Ubuntu")
    while apt_busy():
        if time.monotonic() > deadline:
            raise ValueError("Другая установка пакетов не завершилась за 10 минут; повторите команду позже")
        time.sleep(APT_POLL_SECONDS)


def self_signed(tls, address, authority):
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-nodes",
            "-days",
            "365",
            "-subj",
            "/CN=" + address,
            "-addext",
            "subjectAltName=" + subject(address),
            "-keyout",
            str(tls / "privkey.pem"),
            "-out",
            str(tls / "fullchain.pem"),
        ],
        capture_output=True,
    )
    shutil.copyfile(tls / "fullchain.pem", authority)
    authority.chmod(0o644)


def ask_server(values):
    def port(value):
        return str(HELPERS["port"](value))

    _, https, http = LISTENERS["server"]
    suggested = (output(["hostname", "-I"]).split() or [""])[0]
    address = HELPERS["prompt"]("IP-адрес или домен сервера для браузера", suggested, server_address)
    https = int(HELPERS["prompt"]("HTTPS-порт сайта", str(https), port))
    http = int(HELPERS["prompt"]("HTTP-порт перехода на HTTPS", str(http), port))
    if https == http:
        raise ValueError("HTTPS и HTTP должны использовать разные порты")
    values["PUBLIC_URL"] = public_url(address, https)
    return address, https, http


def ask_public(values):
    values["PUBLIC_URL"] = "https://" + HELPERS["prompt"]("Домен сайта", check=HELPERS["hostname"])
    values["ADMIN_EMAIL"] = HELPERS["prompt"]("Email первого администратора", check=HELPERS["email"])
    values["SMTP_HOST"] = HELPERS["prompt"]("Сервер SMTP", check=HELPERS["hostname"])
    values["SMTP_PORT"] = HELPERS["prompt"]("Порт SMTP", "587", lambda value: str(HELPERS["port"](value)))
    values["SMTP_USER"] = HELPERS["prompt"]("Логин SMTP")
    values["SMTP_PASS"] = HELPERS["prompt"]("Пароль SMTP", hidden=True)
    values["SMTP_FROM"] = HELPERS["prompt"]("Разрешённый SMTP-отправитель", check=HELPERS["email"])
    values["OFFICE_EMAIL"] = HELPERS["prompt"]("Email получателя заявок", check=HELPERS["email"])
    certificate = pathlib.Path(HELPERS["prompt"]("Абсолютный путь к fullchain.pem")).resolve(strict=True)
    key = pathlib.Path(HELPERS["prompt"]("Абсолютный путь к privkey.pem")).resolve(strict=True)
    return certificate, key


def install(config, local):
    from mariadb_native import deploy

    HELPERS["config_directory"](config)
    metadata_path = config / "install.json"
    if metadata_path.exists():
        for name in FILES:
            HELPERS["private_file"](config / name)
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("version") != 2 or metadata.get("stack") != "mariadb":
            raise ValueError("Неизвестная установка; автоматическая смена базы запрещена")
        if local and metadata["mode"] != "local":
            raise ValueError("--local не меняет существующую установку")
        print("Конфигурация уже существует; секреты и доступ администратора сохраняются")
    else:
        if config.exists() and any(config.iterdir()):
            raise ValueError("Каталог содержит прежнюю конфигурацию; файлы не перезаписаны")
        mode = "local"
        if not local:
            mode = HELPERS["prompt"](
                "Контур: 1 – локальная репетиция, 2 – публичный HTTPS, 3 – сервер по IP с самоподписанным сертификатом",
                "1",
                contour,
            )
        values = HELPERS["configuration"](True)
        bind, https, http = LISTENERS[mode]
        address = "localhost"
        certificate = key = None
        if mode == "server":
            address, https, http = ask_server(values)
        if mode == "public":
            certificate, key = ask_public(values)
        busy = busy_ports((https, http) if mode == "public" else (https, http, MAIL_PORT))
        if busy:
            raise ValueError("Порты уже заняты другим сервисом: " + ", ".join(map(str, busy)))
        wait_for_apt()
        run(["bash", str(REPO / "scripts/setup-ubuntu.sh")])
        wait_for_apt()
        run(
            [
                "apt-get",
                "-o",
                "DPkg::Lock::Timeout=" + str(APT_WAIT_SECONDS),
                "install",
                "-y",
                "--no-install-recommends",
                "mariadb-server",
                "mariadb-client",
            ]
        )
        run(["systemctl", "enable", "--now", "mariadb"])
        addresses = output(["ss", "-H", "-lnt", "sport = :3306"]).splitlines()
        for line in addresses:
            address = line.split()[3].rsplit(":", 1)[0].strip("[]")
            if not ipaddress.ip_address(address).is_loopback:
                raise ValueError("MariaDB слушает внешний адрес; настройте loopback перед установкой")
        suffix = secrets.token_hex(6)
        database = "club_" + suffix
        operator, api = database + "_ops", database + "_api"
        operator_password = secrets.token_hex(32)
        project = "club-mariadb-" + suffix
        if sql("SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='" + database + "';") != "0":
            raise ValueError("База уже существует; выберите отдельную установку")
        directory(config)
        data_path = pathlib.Path("/var/lib/club") if config == pathlib.Path("/etc/club") else config / "data"
        if data_path.exists() and any(data_path.iterdir()):
            raise ValueError("Каталог данных уже используется; прежние файлы не присоединяются автоматически")
        data = directory(data_path)
        uploads = directory(data / "uploads", 1000, 1000)
        transfer = directory(data / "transfer", 1000, 1000)
        tls = directory(data / "tls", 0, 101, 0o750)
        if mode == "public":
            shutil.copyfile(key, tls / "privkey.pem")
            shutil.copyfile(certificate, tls / "fullchain.pem")
        else:
            self_signed(tls, address, config / "local-ca.crt")
        os.chown(tls / "privkey.pem", 0, 101)
        (tls / "privkey.pem").chmod(0o640)
        (tls / "fullchain.pem").chmod(0o644)
        create_database(database, operator, operator_password)
        runtime = {
            name: value
            for name, value in values.items()
            if name
            not in (
                "ADMIN_EMAIL",
                "ADMIN_PASSWORD",
                "BACKUP_ENCRYPTION_KEY",
                "BACKUP_OFFSITE_REMOTE",
                "BACKUP_KEEP_DAYS",
                "WEB_DOMAIN",
                "ADMIN_DOMAIN",
                "ACME_EMAIL",
            )
            and not name.startswith(("POSTGRES_", "CHECKOUT_DB_", "TEST_"))
        }
        runtime["CHECKOUT_DATABASE_URL"] = connection_url(api, values["CHECKOUT_DB_PASSWORD"], database)
        operator_values = {
            **runtime,
            "DATABASE_URL": connection_url(operator, operator_password, database),
            "CHECKOUT_DATABASE_URL": connection_url(operator, operator_password, database),
            "CHECKOUT_DB_USER": api,
            "CHECKOUT_DB_HOST": "localhost",
            "CHECKOUT_DB_PASSWORD": values["CHECKOUT_DB_PASSWORD"],
            "ADMIN_EMAIL": values["ADMIN_EMAIL"],
            "ADMIN_PASSWORD": values["ADMIN_PASSWORD"],
        }
        compose_values = {
            "CLUB_STACK": "mariadb",
            "CLUB_PROJECT": project,
            "CLUB_NETWORK": project,
            "CLUB_DATABASE": database,
            "CLUB_RUNTIME_ENV": str(config / "runtime.env"),
            "CLUB_OPERATOR_ENV": str(config / "operator.env"),
            "CLUB_UPLOADS_DIR": str(uploads),
            "CLUB_TRANSFER_DIR": str(transfer),
            "CLUB_TLS_DIR": str(tls),
            "CLUB_STATE_DIR": "/var/lib/club-ops" if config == pathlib.Path("/etc/club") else str(config / "state"),
            "CLUB_BACKUP_DIR": "/var/backups/club" if config == pathlib.Path("/etc/club") else str(config / "backups"),
            "CLUB_API_IMAGE": project + "-api:local",
            "CLUB_WEB_IMAGE": project + "-web:local",
            "CLUB_OPERATOR_IMAGE": project + "-operator:local",
            "CLUB_NGINX_IMAGE": project + "-nginx:local",
            "PUBLIC_URL": values["PUBLIC_URL"],
            "CLUB_BIND_ADDRESS": bind,
            "CLUB_HTTP_PORT": str(http),
            "CLUB_HTTPS_PORT": str(https),
            "COMPOSE_PROFILES": "" if mode == "public" else "local",
            "BACKUP_ENCRYPTION_KEY": values["BACKUP_ENCRYPTION_KEY"],
            "BACKUP_KEEP_DAYS": "14",
            "BACKUP_OFFSITE_REMOTE": "",
            "HTTPS_CA_FILE": "" if mode == "public" else str(config / "local-ca.crt"),
        }
        metadata = {
            "version": 2,
            "stack": "mariadb",
            "mode": mode,
            "database": database,
            "operator": operator,
            "api": api,
            "project": project,
        }
        payloads = {
            "runtime.env": HELPERS["dotenv"](runtime),
            "operator.env": HELPERS["dotenv"](operator_values),
            "compose.env": HELPERS["dotenv"](compose_values),
            "operations.env": HELPERS["dotenv"]({"ENV_FILE": str(config / "compose.env"), "CLUB_STACK": "mariadb"}),
            "initial-admin.txt": "Email: " + values["ADMIN_EMAIL"] + "\nPassword: " + values["ADMIN_PASSWORD"] + "\n",
            "install.json": json.dumps(metadata) + "\n",
        }
        for name, content in payloads.items():
            HELPERS["write_new"](config / name, content)
    context = Context(config / "compose.env")
    with context.lock():
        deploy(context)
    print("Офис: " + context.runtime["PUBLIC_URL"] + "/admin")
    print("Первичные данные администратора: sudo cat " + shlex.quote(str(config / "initial-admin.txt")))
    if metadata["mode"] == "local":
        print("Локальная CA: " + str(config / "local-ca.crt") + "; письма: http://localhost:8025")
    if metadata["mode"] == "server":
        print("Самоподписанный сертификат: браузер один раз попросит подтвердить исключение")
        print("CA для доверия на компьютерах: " + str(config / "local-ca.crt"))


def prepare_restore(context, config, port, mail_port):
    HELPERS["config_directory"](config)
    if config.exists() and any(config.iterdir()):
        raise ValueError("Для восстановления нужен новый каталог конфигурации")
    HELPERS["port"](str(port))
    HELPERS["port"](str(mail_port))
    if (
        port >= 65535
        or mail_port in (port, port + 1)
        or port == int(urlsplit(context.runtime["PUBLIC_URL"]).port or 443)
    ):
        raise ValueError("Выберите отдельные порты стенда восстановления")
    directory(config)
    suffix = secrets.token_hex(6)
    database = "club_restore_" + suffix
    operator, api = database + "_ops", database + "_api"
    password, api_password = secrets.token_hex(32), secrets.token_hex(32)
    create_database(database, operator, password)
    runtime = {
        **context.runtime,
        "CHECKOUT_DATABASE_URL": connection_url(api, api_password, database),
        "PUBLIC_URL": "https://localhost:" + str(port),
        "SMTP_HOST": "mailpit",
        "SMTP_PORT": "1025",
        "SMTP_USER": "",
        "SMTP_PASS": "",
        "OFFICE_NOTIFY_CHANNEL": "email",
    }
    for name in ("JOBS_ENABLED", "DPO_SYNC_ENABLED", "NEWS_SYNC_ENABLED", "SUPPORT_ENABLED", "TELEGRAM_POLLING"):
        runtime[name] = "false"
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "YOOKASSA_SHOP_ID",
        "YOOKASSA_SECRET_KEY",
        "VAPID_PUBLIC_KEY",
        "VAPID_PRIVATE_KEY",
        "OFFICE_TG_BOT_TOKEN",
        "OFFICE_TG_CHAT_ID",
        "SENTRY_DSN",
        "POINTS_SERVICE_TOKEN",
    ):
        runtime[name] = ""
    operator_values = {
        **context.operator,
        **runtime,
        "DATABASE_URL": connection_url(operator, password, database),
        "CHECKOUT_DATABASE_URL": connection_url(operator, password, database),
        "CHECKOUT_DB_USER": api,
        "CHECKOUT_DB_PASSWORD": api_password,
    }
    values = {
        **context.values,
        "CLUB_DATABASE": database,
        "CLUB_PROJECT": "club-mariadb-restore-" + suffix,
        "CLUB_NETWORK": "club-mariadb-restore-" + suffix,
        "CLUB_RUNTIME_ENV": str(config / "runtime.env"),
        "CLUB_OPERATOR_ENV": str(config / "operator.env"),
        "CLUB_UPLOADS_DIR": str(directory(config / "uploads", 1000, 1000)),
        "CLUB_TRANSFER_DIR": str(directory(config / "transfer", 1000, 1000)),
        "CLUB_STATE_DIR": str(config / "state"),
        "CLUB_BACKUP_DIR": str(config / "backups"),
        "CLUB_BIND_ADDRESS": "127.0.0.1",
        "CLUB_HTTPS_PORT": str(port),
        "CLUB_HTTP_PORT": str(port + 1),
        "CLUB_MAIL_PORT": str(mail_port),
        "PUBLIC_URL": runtime["PUBLIC_URL"],
        "COMPOSE_PROFILES": "local",
    }
    tls = directory(config / "tls", 0, 101, 0o750)
    self_signed(tls, "localhost", config / "local-ca.crt")
    os.chown(tls / "privkey.pem", 0, 101)
    (tls / "privkey.pem").chmod(0o640)
    (tls / "fullchain.pem").chmod(0o644)
    values.update(CLUB_TLS_DIR=str(tls), HTTPS_CA_FILE=str(config / "local-ca.crt"))
    for name, payload in (("runtime.env", runtime), ("operator.env", operator_values), ("compose.env", values)):
        HELPERS["write_new"](config / name, HELPERS["dotenv"](payload))
    print("Пустой изолированный контур подготовлен: " + str(config / "compose.env"))
