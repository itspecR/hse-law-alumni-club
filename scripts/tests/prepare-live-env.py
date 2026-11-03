import os
import secrets
import sys
from pathlib import Path


def prepare(destination, directory):
    values = {
        "APP_ENV": "development",
        "SEED_DEMO": "false",
        "JOBS_ENABLED": "false",
        "DPO_SYNC_ENABLED": "false",
        "POSTGRES_DB": "club_ci_live",
        "POSTGRES_USER": "club",
        "CHECKOUT_DB_USER": "club_api",
        "ADMIN_EMAIL": "admin@live.example.com",
        "TEST_EDITOR_EMAIL": "editor@live.example.com",
        "TEST_ALUMNI_EMAIL": "unused@live.example.com",
        "PUBLIC_URL": "http://127.0.0.1:8180",
        "WEB_DOMAIN": ":80",
        "ADMIN_DOMAIN": ":8081",
        "ACME_EMAIL": "admin@live.example.com",
        "SMTP_HOST": "mailpit",
        "SMTP_PORT": "1025",
        "SMTP_FROM": "club@live.example.com",
        "SMTP_USER": "",
        "SMTP_PASS": "",
        "OFFICE_NOTIFY_CHANNEL": "email",
        "OFFICE_EMAIL": "office@live.example.com",
        "OFFICE_TG_BOT_TOKEN": "",
        "OFFICE_TG_CHAT_ID": "",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_POLLING": "false",
        "YOOKASSA_SHOP_ID": "",
        "YOOKASSA_SECRET_KEY": "",
        "VAPID_PUBLIC_KEY": "",
        "VAPID_PRIVATE_KEY": "",
        "SENTRY_DSN": "",
        "NEWS_SYNC_ENABLED": "false",
        "SUPPORT_ENABLED": "false",
        "E2E_LIVE_AUTHORIZED": "club-ci-live",
        "E2E_BASE_URL": "http://127.0.0.1:8180",
        "E2E_MAIL_URL": "http://127.0.0.1:8182",
        "E2E_STATE_DIR": str(Path(directory).resolve()),
    }
    for name in (
        "POSTGRES_PASSWORD",
        "CHECKOUT_DB_PASSWORD",
        "ADMIN_PASSWORD",
        "AUTH_SECRET",
        "ADMIN_AUTH_SECRET",
        "TEST_EDITOR_PASSWORD",
        "TEST_ALUMNI_PASSWORD",
        "E2E_LIVE_PASSWORD",
        "E2E_LIVE_NEW_PASSWORD",
        "E2E_CUSTOM_EDITOR_PASSWORD",
        "E2E_CUSTOM_SERVICE_PASSWORD",
    ):
        values[name] = secrets.token_hex(32)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write("".join(f"{key}={value}\n" for key, value in values.items()))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for key, value in values.items():
            if any(word in key for word in ("PASSWORD", "SECRET", "TOKEN")) and value:
                print(f"::add-mask::{value}")


if __name__ == "__main__":
    prepare(*sys.argv[1:])
