import argparse
import asyncio
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from club_ops.alumni import create_alumni
from club_ops.bootstrap import BootstrapConfig, OperatorError, bootstrap
from club_ops.office import Office
from club_ops.podcasts import import_podcasts
from club_ops.staff import manage_staff


@asynccontextmanager
async def operator_connection(values):
    url = values.get("DATABASE_URL", "")
    if url.startswith(("mariadb://", "mysql://")):
        from club_api.asgi import initialize_django
        from club_api.core.config import Settings
        from club_api.db.pool import Database

        os.environ["CHECKOUT_DATABASE_URL"] = url
        initialize_django()
        database = Database(
            Settings(AUTH_SECRET=values.get("AUTH_SECRET") or secrets.token_urlsafe(48), CHECKOUT_DATABASE_URL=url)
        )
        async with database.connection() as connection:
            yield connection
    else:
        async with await AsyncConnection.connect(url, row_factory=dict_row, connect_timeout=10) as connection:
            yield connection


PASSWORD_INPUTS = {"manage-staff": "STAFF_PASSWORD", "create-alumni": "ALUMNI_PASSWORD"}


async def database_command(command, values):
    secret = PASSWORD_INPUTS.get(command)
    if secret and not values.get(secret) and not sys.stdin.isatty():
        values[secret] = sys.stdin.read(102).removesuffix("\n").removesuffix("\r")
    async with operator_connection(values) as connection:
        if command == "migrate":
            if getattr(connection, "vendor", None) != "mysql":
                raise OperatorError("Миграции Django здесь применяются только к новой MariaDB")
            from django.core.management import call_command

            await connection.run(call_command, "check", databases=["default"])
            await connection.run(call_command, "migrate", interactive=False)
            return
        if command == "configure-mariadb-role":
            if getattr(connection, "vendor", None) != "mysql":
                raise OperatorError("Команда предназначена для MariaDB")
            from club_ops.mariadb_roles import configure_runtime_role

            await connection.run(configure_runtime_role, values)
            print("Роль MariaDB настроена; права ограничены таблицами и полями API")
            return
        if command == "database-fingerprints":
            import json

            from club_ops.database_transfer import fingerprint, model_tables, table_rows

            def fingerprints():
                return {name: fingerprint(table_rows(model)) for name, model in model_tables().items()}

            print(json.dumps(await connection.run(fingerprints), sort_keys=True))
            return
        if command == "bootstrap":
            result = await bootstrap(connection, BootstrapConfig.from_env(values))
            print(
                f"Bootstrap завершён: аккаунтов {result['createdUsers']}, записей {result['createdRows']}, главная создана: {result['createdHome']}"
            )
        elif command == "create-alumni":
            address = await create_alumni(
                connection,
                address=values.get("ALUMNI_EMAIL", ""),
                fio=values.get("ALUMNI_FIO", ""),
                password=values.get("ALUMNI_PASSWORD", ""),
            )
            print(f"Выпускник {address} создан: почта и статус выпускника подтверждены")
        else:
            result = await manage_staff(
                connection,
                action=values.get("STAFF_ACTION", "create"),
                address=values.get("STAFF_EMAIL", ""),
                role=values.get("STAFF_ROLE", "editor"),
                password=values.get("STAFF_PASSWORD", ""),
            )
            print(
                "Аккаунт сотрудника создан"
                if result == "created"
                else "Пароль сотрудника обновлён; прежние сессии отозваны"
            )


async def setup_webhook(values):
    token, secret = values.get("TELEGRAM_BOT_TOKEN"), values.get("TELEGRAM_WEBHOOK_SECRET")
    public = values.get("PUBLIC_URL", "").rstrip("/")
    url = urlsplit(public)
    if (
        not token
        or not secret
        or url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise OperatorError("Нужны TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET и корневой PUBLIC_URL с HTTPS")
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        response = await client.post(
            "https://api.telegram.org/bot" + token + "/setWebhook",
            json={
                "url": public + "/api/telegram/webhook",
                "secret_token": secret,
                "allowed_updates": ["message", "message_reaction"],
            },
        )
        if not response.is_success or response.json().get("ok") is not True:
            raise OperatorError("Telegram не подтвердил установку вебхука")
    print("Вебхук Telegram установлен")


async def main(args):
    values = dict(os.environ)
    if args.command in ("export-postgres", "import-postgres"):
        from club_api.asgi import initialize_django
        from club_ops.database_transfer import export_snapshot, import_snapshot

        os.environ["CHECKOUT_DATABASE_URL"] = (
            ""
            if args.command == "export-postgres"
            else values.get("DATABASE_URL", values.get("CHECKOUT_DATABASE_URL", ""))
        )
        initialize_django()
        if args.command == "export-postgres":
            tables = await asyncio.to_thread(export_snapshot, args.snapshot, values.get("SOURCE_DATABASE_URL", ""))
        else:
            tables = (await asyncio.to_thread(import_snapshot, args.snapshot))["tables"]
        print(f"{args.command}: таблиц {len(tables)}, записей {sum(table['rows'] for table in tables.values())}")
    elif args.command in (
        "bootstrap",
        "manage-staff",
        "create-alumni",
        "migrate",
        "configure-mariadb-role",
        "database-fingerprints",
    ):
        await database_command(args.command, values)
    elif args.command == "setup-telegram-webhook":
        await setup_webhook(values)
    else:
        office = Office(values)
        try:
            if args.command == "import-podcasts":
                await import_podcasts(office, args.manifest)
            else:
                result = await office.request("dpo-sync", "POST")
                print(
                    f"Каталог обновлён: создано {result['created']}, обновлено {result['updated']}, архивировано {result['archived']}"
                )
        finally:
            await office.close()


def run():
    parser = argparse.ArgumentParser(description="Команды оператора Клуба выпускников")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "bootstrap",
        "manage-staff",
        "create-alumni",
        "migrate",
        "configure-mariadb-role",
        "database-fingerprints",
        "sync-dpo",
        "setup-telegram-webhook",
    ):
        sub.add_parser(name)
    sub.add_parser("import-podcasts").add_argument("manifest")
    for name in ("export-postgres", "import-postgres"):
        sub.add_parser(name).add_argument("snapshot")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    try:
        asyncio.run(main(args))
    except OperatorError as error:
        parser.exit(1, str(error) + "\n")
    except Exception:
        parser.exit(1, "Команда не завершена. Проверьте параметры, доступ и журнал сервера\n")


if __name__ == "__main__":
    run()
