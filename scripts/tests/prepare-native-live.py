import base64
import hashlib
import json
import os
import pathlib
import secrets
import shlex
import subprocess
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
from mariadb_common import HELPERS, Context, output, sql

LIVE_RATE_LIMIT = "5000"


def prepare(config, destination):
    config = pathlib.Path(config).resolve()
    destination = pathlib.Path(destination).resolve()
    metadata = json.loads((config / "install.json").read_text())
    if metadata.get("version") != 2 or metadata.get("mode") != "local":
        raise ValueError("Нужен отдельный новый локальный стенд установки")
    context = Context(config / "compose.env")
    if urlsplit(context.runtime["PUBLIC_URL"]).hostname not in ("localhost", "127.0.0.1"):
        raise ValueError("Нужен локальный адрес тестового стенда")
    if sql("SELECT COUNT(*) FROM alumni", context.database) != "0":
        raise ValueError("Live-фикстуры создаются только на стенде без выпускников")
    if any(context.runtime.get(name) for name in ("TELEGRAM_BOT_TOKEN", "YOOKASSA_SECRET_KEY", "VAPID_PRIVATE_KEY")):
        raise ValueError("Внешние интеграции тестового стенда должны быть отключены")
    credentials = dict(line.split(": ", 1) for line in (config / "initial-admin.txt").read_text().splitlines())
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    values = {
        "E2E_LIVE_AUTHORIZED": "club-ci-live",
        "E2E_BASE_URL": context.runtime["PUBLIC_URL"],
        "E2E_MAIL_URL": "http://localhost:" + context.values.get("CLUB_MAIL_PORT", "8025"),
        "E2E_STATE_DIR": str(destination),
        "ADMIN_EMAIL": credentials["Email"],
        "ADMIN_PASSWORD": credentials["Password"],
        "TEST_EDITOR_EMAIL": "editor@live.example.com",
    }
    for key in (
        "TEST_EDITOR_PASSWORD",
        "E2E_LIVE_PASSWORD",
        "E2E_LIVE_NEW_PASSWORD",
        "E2E_CUSTOM_EDITOR_PASSWORD",
        "E2E_CUSTOM_SERVICE_PASSWORD",
    ):
        values[key] = secrets.token_hex(32)
    certificate = config / "local-ca.crt"
    public = output(["openssl", "x509", "-in", str(certificate), "-pubkey", "-noout"])
    key = subprocess.run(
        ["openssl", "pkey", "-pubin", "-outform", "DER"], input=public.encode(), capture_output=True, check=True
    ).stdout
    values["E2E_TLS_SPKI"] = base64.b64encode(hashlib.sha256(key).digest()).decode()
    values["NODE_EXTRA_CA_CERTS"] = str(certificate)
    runtime = {
        **context.runtime,
        "RATE_LIMIT_MAX": LIVE_RATE_LIMIT,
        "SUPPORT_ENABLED": "true",
        "SUPPORT_OPERATOR_NAME": "Локальный тестовый оператор",
        "SUPPORT_OPERATOR_ADDRESS": "Локальный стенд без реального адреса",
        "SUPPORT_OPERATOR_CONTACT": "test@live.example.com",
    }
    runtime_path = pathlib.Path(context.values["CLUB_RUNTIME_ENV"])
    runtime_path.write_text(HELPERS["dotenv"](runtime))
    runtime_path.chmod(0o600)
    context.execute("up", "-d", "--wait", "--force-recreate", "api")
    context.execute("up", "-d", "--wait", "--force-recreate", "nginx")
    context.verify_http()
    staff = {
        **context.env,
        "STAFF_ACTION": "create",
        "STAFF_ROLE": "editor",
        "STAFF_EMAIL": values["TEST_EDITOR_EMAIL"],
        "STAFF_PASSWORD": values["TEST_EDITOR_PASSWORD"],
    }
    output(
        [
            *context.compose,
            "run",
            "--rm",
            "-T",
            "-e",
            "STAFF_ACTION",
            "-e",
            "STAFF_ROLE",
            "-e",
            "STAFF_EMAIL",
            "-e",
            "STAFF_PASSWORD",
            "operator",
            "python",
            "-m",
            "club_ops.cli",
            "manage-staff",
        ],
        env=staff,
    )
    for index, (prefix, action, device) in enumerate(
        (
            ("a", "Скрыть", "desktop"),
            ("a", "Опубликовать", "desktop"),
            ("b", "Скрыть", "mobile"),
            ("b", "Опубликовать", "mobile"),
        ),
        1,
    ):
        identifier = prefix * 63 + ("1" if action == "Скрыть" else "2")
        sql(
            f"INSERT INTO club_news_inbox(id,source_url,sources,title,published_at) VALUES "
            f"('{identifier}','https://pravo.hse.ru/news/100000000{index}.html','[\"alumni\"]',"
            f"'{action} новость {device}',UTC_TIMESTAMP());",
            context.database,
        )
    sql(
        "INSERT INTO club_news_source_runs(source,error) VALUES('alumni','Тестовая ошибка загрузки источника');",
        context.database,
    )
    descriptor = os.open(destination / "browser.env", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(name + "=" + shlex.quote(value) + "\n" for name, value in values.items()))
    print("Локальные live-фикстуры и закрытая конфигурация подготовлены")


def restart(config):
    context = Context(pathlib.Path(config) / "compose.env")
    before = sql("SELECT id,email,password,token,role,status FROM directus_users ORDER BY id", context.database)
    for _ in range(2):
        context.operator_command("bootstrap")
        context.operator_command("migrate")
        after = sql("SELECT id,email,password,token,role,status FROM directus_users ORDER BY id", context.database)
        if before != after:
            raise ValueError("Bootstrap изменил учётные записи")
    context.execute("restart", "api", "web", "mailpit")
    context.execute("up", "-d", "--wait", "api", "web", "mailpit")
    context.execute("restart", "nginx")
    context.verify_http()
    print("Bootstrap сохранил пароли, роли и сессии; рестарт завершён")


if __name__ == "__main__":
    os.umask(0o077)
    if sys.argv[1] == "prepare":
        prepare(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "restart":
        restart(sys.argv[2])
    else:
        raise ValueError("Неизвестный этап")
