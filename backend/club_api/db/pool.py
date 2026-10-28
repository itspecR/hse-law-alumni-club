from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from club_api.core.config import Settings
from club_api.core.errors import ApiError


class Database:
    vendor = "postgresql"

    def __new__(cls, settings):
        if settings.secret("CHECKOUT_DATABASE_URL").startswith(("mariadb://", "mysql://")):
            from club_api.db.mysql import MariaDatabase

            return MariaDatabase(settings)
        return super().__new__(cls)

    def __init__(self, settings: Settings):
        url = settings.secret("CHECKOUT_DATABASE_URL")
        self.pool = (
            AsyncConnectionPool(
                conninfo=url,
                min_size=0,
                max_size=6,
                timeout=5,
                open=False,
                kwargs={
                    "autocommit": True,
                    "row_factory": dict_row,
                    "connect_timeout": 5,
                    "options": "-c statement_timeout=15000",
                },
            )
            if url
            else None
        )

    async def open(self):
        if self.pool:
            await self.pool.open()

    async def close(self):
        if self.pool:
            await self.pool.close()

    @asynccontextmanager
    async def connection(self):
        if not self.pool:
            raise ApiError(503, "Транзакционное оформление не настроено.")
        async with self.pool.connection() as connection:
            yield connection

    @asynccontextmanager
    async def transaction(self):
        async with self.connection() as connection, connection.transaction():
            yield connection

    async def rows(self, query: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        async with self.connection() as connection:
            cursor = await connection.execute(query, params)
            return await cursor.fetchall()

    async def execute(self, query: str, params: tuple | list = ()) -> int:
        async with self.connection() as connection:
            return (await connection.execute(query, params)).rowcount
