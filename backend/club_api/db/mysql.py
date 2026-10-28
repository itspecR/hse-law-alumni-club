import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from django.db import ConnectionHandler, connections

from club_api.core.errors import ApiError
from club_api.db.config import database_config

JSON_FIELDS = frozenset(
    {
        "rule_json",
        "contacts_json",
        "interests_json",
        "detail",
        "marquee",
        "items_json",
        "images",
        "variants_json",
        "dates",
        "modules",
        "teachers",
        "audience",
        "results",
        "advantages",
        "keys",
        "reservations",
        "receipt",
        "messages",
        "metadata",
        "tus_data",
        "auth_data",
        "tags",
        "value",
        "theme_light_overrides",
        "theme_dark_overrides",
        "sources",
    }
)


def parameter(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo:
        return value.astimezone(UTC).replace(tzinfo=None)
    if type(value).__module__ == "psycopg.types.json":
        return json.dumps(value.obj, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list | dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def record(columns, values):
    row = dict(zip(columns, values, strict=True))
    for key, value in row.items():
        if isinstance(value, datetime) and value.tzinfo is None:
            row[key] = value.replace(tzinfo=UTC)
        elif key in JSON_FIELDS and isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except ValueError:
                pass
    return row


class Cursor:
    def __init__(self, rows, rowcount):
        self.rows, self.rowcount = rows, rowcount

    async def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    async def fetchall(self):
        rows, self.rows = self.rows, []
        return rows


class Connection:
    vendor = "mysql"

    def __init__(self, config):
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="club-database")
        self.connection = None
        self.in_transaction = False

    async def run(self, function, *args, **kwargs):
        future = asyncio.get_running_loop().run_in_executor(self.executor, lambda: function(*args, **kwargs))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await future
            raise

    def connect(self):
        self.connection = ConnectionHandler({"default": self.config})["default"]
        connections["default"] = self.connection
        self.connection.ensure_connection()
        with self.connection.cursor() as cursor:
            cursor.execute("SET SESSION max_statement_time=15, innodb_lock_wait_timeout=5")

    def query(self, query, params):
        with self.connection.cursor() as cursor:
            cursor.execute(getattr(query, "mariadb", query), tuple(map(parameter, params)))
            rows = (
                [record([column[0] for column in cursor.description], row) for row in cursor.fetchall()]
                if cursor.description
                else []
            )
            return Cursor(rows, cursor.rowcount)

    async def execute(self, query, params=()):
        return await self.run(self.query, query, params)

    @asynccontextmanager
    async def transaction(self):
        if self.in_transaction:
            raise RuntimeError("Вложенная транзакция не поддерживается")
        await self.run(self.connection.set_autocommit, False)
        self.in_transaction = True
        try:
            yield self
        except BaseException:
            await self.run(self.connection.rollback)
            raise
        else:
            await self.run(self.connection.commit)
        finally:
            self.in_transaction = False
            await self.run(self.connection.set_autocommit, True)

    async def lock(self, key, *, wait=5):
        name = hashlib.sha256((self.config["NAME"] + ":" + str(key)).encode()).hexdigest()
        row = await (await self.execute("SELECT GET_LOCK(%s,%s) AS acquired", (name, wait))).fetchone()
        if row["acquired"] != 1 and wait:
            raise ApiError(503, "Данные сейчас изменяются. Повторите действие позже.")
        return row["acquired"] == 1

    async def unlock(self, key):
        name = hashlib.sha256((self.config["NAME"] + ":" + str(key)).encode()).hexdigest()
        await self.execute("SELECT RELEASE_LOCK(%s)", (name,))

    async def close(self):
        if self.connection:
            await self.run(self.connection.close)
        self.executor.shutdown(wait=True)


class MariaDatabase:
    vendor = "mysql"

    def __init__(self, settings):
        self.config = database_config(settings.secret("CHECKOUT_DATABASE_URL"))
        self.pool = True
        self.slots = asyncio.Semaphore(6)

    async def open(self):
        await self.rows("SELECT 1 AS ready")

    async def close(self):
        pass

    @asynccontextmanager
    async def connection(self):
        async with self.slots:
            connection = Connection(self.config)
            try:
                await connection.run(connection.connect)
                yield connection
            finally:
                await connection.close()

    @asynccontextmanager
    async def transaction(self):
        async with self.connection() as connection, connection.transaction():
            yield connection

    async def rows(self, query, params=()):
        async with self.connection() as connection:
            return await (await connection.execute(query, params)).fetchall()

    async def execute(self, query, params=()):
        async with self.connection() as connection:
            return (await connection.execute(query, params)).rowcount

    async def orm(self, function):
        async with self.transaction() as connection:
            return await connection.run(function)
