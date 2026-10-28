import re
from datetime import UTC, date, datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from club_api.core.errors import ApiError
from club_api.db.pool import Database
from club_api.db.schema import DATA_COLUMNS, JSON_COLUMNS

BLOCK_FIELDS = frozenset(("blocks.collection", "blocks.sort", "blocks.item:block_hero.*", "blocks.item:block_cta.*"))
MEDIA_FIELDS = {
    "alumni": ("avatar",),
    "events": ("cover", "description"),
    "news": ("body",),
    "podcasts": ("cover", "audio_url", "description"),
    "products": ("images", "description"),
    "programs": ("cover", "teachers", "modules", "description", "results", "advantages", "audience"),
    "trainers": ("photo", "bio"),
}
MEDIA_LINK = re.compile(r"/(?:api/media|assets)/([0-9a-f-]{36})", re.I)


def invalid():
    return ValueError("Недопустимый внутренний запрос данных")


def columns(table):
    if not isinstance(table, str) or table not in DATA_COLUMNS:
        raise invalid()
    return DATA_COLUMNS[table]


def field(table, name):
    if name not in columns(table):
        raise invalid()
    return f'"{name}"'


def normalize(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value


def predicate(table, filters, params, depth=0):
    columns(table)
    if filters is None:
        return "TRUE"
    if not isinstance(filters, dict) or depth > 12:
        raise invalid()
    clauses = []
    for name, condition in filters.items():
        if name in ("_and", "_or"):
            if not isinstance(condition, list) or len(condition) > 1000:
                raise invalid()
            parts = [predicate(table, part, params, depth + 1) for part in condition]
            clauses.append(
                (" AND " if name == "_and" else " OR ").join(f"({part})" for part in parts)
                or ("TRUE" if name == "_and" else "FALSE")
            )
            continue
        column = field(table, name)
        if not isinstance(condition, dict):
            raise invalid()
        for operator, value in condition.items():
            comparisons = {"_eq": "=", "_neq": "<>", "_gt": ">", "_gte": ">=", "_lt": "<", "_lte": "<="}
            if operator in comparisons:
                if isinstance(value, dict | list):
                    raise invalid()
                if value is None and operator in ("_eq", "_neq"):
                    clauses.append(f"{column} IS {'NOT ' if operator == '_neq' else ''}NULL")
                else:
                    clauses.append(f"{column} {comparisons[operator]} %s")
                    params.append(value)
            elif operator in ("_null", "_nnull"):
                if not isinstance(value, bool):
                    raise invalid()
                clauses.append(f"{column} IS {'NOT ' if (operator == '_nnull') == value else ''}NULL")
            elif operator in ("_in", "_nin"):
                if not isinstance(value, list) or len(value) > 20000 or any(isinstance(v, dict | list) for v in value):
                    raise invalid()
                if not value:
                    clauses.append("FALSE" if operator == "_in" else "TRUE")
                else:
                    clauses.append(
                        f"{column} {'NOT ' if operator == '_nin' else ''}IN ({','.join(['%s'] * len(value))})"
                    )
                    params.extend(value)
            elif operator in ("_icontains", "_contains", "_starts_with"):
                if not isinstance(value, str):
                    raise invalid()
                escaped = re.sub(r"[\\%_]", lambda match: "\\" + match[0], value)
                params.append(("" if operator == "_starts_with" else "%") + escaped + "%")
                clauses.append(f"{column} {'ILIKE' if operator == '_icontains' else 'LIKE'} %s")
            else:
                raise invalid()
    return " AND ".join(f"({clause})" for clause in clauses) or "TRUE"


def selection(table, fields=None):
    allowed = columns(table)
    fields = ["*"] if fields is None else fields
    if not fields:
        raise invalid()
    names = []
    for name in fields:
        if table == "pages" and name in BLOCK_FIELDS:
            continue
        names.extend([field(table, value) for value in allowed] if name == "*" else [field(table, name)])
    return ",".join(dict.fromkeys(names)) or field(table, "id")


def media_ids(value):
    result = set()
    if isinstance(value, str):
        try:
            result.add(str(UUID(value)))
        except ValueError:
            pass
        for match in MEDIA_LINK.finditer(value):
            try:
                result.add(str(UUID(match[1])))
            except ValueError:
                pass
    elif isinstance(value, list | tuple):
        for item in value:
            result.update(media_ids(item))
    elif isinstance(value, dict):
        for item in value.values():
            result.update(media_ids(item))
    return result


async def assert_media_references(connection, table, data):
    ids = set()
    for name in MEDIA_FIELDS.get(table, ()):
        ids.update(media_ids(data.get(name)))
    if not ids:
        return
    await connection.execute(f'LOCK TABLE "{table}" IN ROW EXCLUSIVE MODE')
    cursor = await connection.execute(
        "SELECT f.id,f.metadata->>'club_upload_kind' AS upload_kind,"
        "EXISTS(SELECT 1 FROM alumni a WHERE a.avatar=f.id::text) AS is_avatar "
        "FROM directus_files f WHERE f.id=ANY(%s::uuid[]) ORDER BY f.id FOR SHARE OF f",
        (sorted(ids),),
    )
    rows = await cursor.fetchall()
    if len(rows) != len(ids):
        raise ApiError(400, "Один из файлов удален. Выберите файл заново.")
    if table != "alumni" and any(row["upload_kind"] == "avatar" or row["is_avatar"] for row in rows):
        raise ApiError(
            400, "Фотографию профиля нельзя использовать в материалах. Загрузите отдельный файл в медиатеку."
        )


class Store:
    def __new__(cls, database):
        if cls is Store and getattr(database, "vendor", None) == "mysql":
            from club_api.db.orm_store import ORMStore

            return super().__new__(ORMStore)
        return super().__new__(cls)

    def __init__(self, database: Database):
        self.database = database

    async def read(self, table, *, filters=None, fields=None, sort=(), limit=100, offset=0, page=None, connection=None):
        columns(table)
        if page is not None:
            offset = (page - 1) * (0 if limit == -1 else limit)
        if type(limit) is not int or limit < -1 or type(offset) is not int or offset < 0:
            raise invalid()
        blocks = table == "pages" and fields and any(name in BLOCK_FIELDS for name in fields)
        hidden_id = blocks and not any(name in ("id", "*") for name in fields)
        projection = [*fields, "id"] if hidden_id else fields
        params = []
        where = predicate(table, filters, params)
        query = f'SELECT {selection(table, projection)} FROM "{table}" WHERE {where}'
        if sort:
            query += " ORDER BY " + ",".join(
                field(table, name.removeprefix("-")) + (" DESC" if name.startswith("-") else " ASC") for name in sort
            )
        if limit != -1:
            query += " LIMIT %s"
            params.append(limit)
        if offset:
            query += " OFFSET %s"
            params.append(offset)
        if connection is None:
            async with self.database.connection() as conn:
                return await self.read(
                    table, filters=filters, fields=fields, sort=sort, limit=limit, offset=offset, connection=conn
                )
        cursor = await connection.execute(query, params)
        rows = await cursor.fetchall()
        if blocks and rows:
            cursor = await connection.execute(
                "SELECT b.pages_id,b.collection,b.sort,CASE b.collection WHEN 'block_hero' THEN to_jsonb(h) "
                "WHEN 'block_cta' THEN to_jsonb(c) END AS item FROM pages_blocks b "
                "LEFT JOIN block_hero h ON b.collection='block_hero' AND b.item=h.id::text "
                "LEFT JOIN block_cta c ON b.collection='block_cta' AND b.item=c.id::text "
                "WHERE b.pages_id=ANY(%s::uuid[]) ORDER BY b.sort,b.id",
                ([row["id"] for row in rows],),
            )
            children = await cursor.fetchall()
            for row in rows:
                row["blocks"] = [
                    {k: v for k, v in child.items() if k != "pages_id"}
                    for child in children
                    if child["pages_id"] == row["id"]
                ]
                if hidden_id:
                    del row["id"]
        return normalize(rows)

    async def one(self, table, id, *, fields=None, connection=None):
        rows = await self.read(table, filters={"id": {"_eq": id}}, fields=fields, limit=1, connection=connection)
        if not rows:
            raise ApiError(404, "Запись не найдена.")
        return rows[0]

    async def create(self, table, data, *, connection=None):
        columns(table)
        if table.startswith("directus_") or not isinstance(data, dict):
            raise invalid()
        names = [field(table, key) for key in data]
        values = [
            Jsonb(value) if key in JSON_COLUMNS.get(table, ()) and value is not None else value
            for key, value in data.items()
        ]
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.create(table, data, connection=conn)
        await assert_media_references(connection, table, data)
        insert = f"({','.join(names)}) VALUES ({','.join(['%s'] * len(names))})" if names else "DEFAULT VALUES"
        cursor = await connection.execute(f'INSERT INTO "{table}" {insert} RETURNING {selection(table)}', values)
        return normalize(await cursor.fetchone())

    async def update(self, table, data, *, id=None, filters=None, connection=None):
        columns(table)
        if table.startswith("directus_") or not isinstance(data, dict) or "id" in data:
            raise invalid()
        if id is None and (not isinstance(filters, dict) or not filters):
            raise invalid()
        assignments = ",".join(f"{field(table, key)}=%s" for key in data)
        params = [
            Jsonb(value) if key in JSON_COLUMNS.get(table, ()) and value is not None else value
            for key, value in data.items()
        ]
        where = predicate(table, {"id": {"_eq": id}} if id is not None else filters, params)
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.update(table, data, id=id, filters=filters, connection=conn)
        if not data:
            return await self.one(table, id, connection=connection) if id is not None else []
        await assert_media_references(connection, table, data)
        query = f'UPDATE "{table}" SET {assignments} WHERE {where} RETURNING {selection(table)}'
        cursor = await connection.execute(query, params)
        rows = normalize(await cursor.fetchall())
        if id is not None and not rows:
            raise ApiError(404, "Запись не найдена.")
        return rows[0] if id is not None else rows

    async def delete(self, table, *, id=None, filters=None, connection=None):
        columns(table)
        if table.startswith("directus_"):
            raise invalid()
        if id is None and (not isinstance(filters, dict) or not filters):
            raise invalid()
        params = []
        where = predicate(table, {"id": {"_eq": id}} if id is not None else filters, params)
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.delete(table, id=id, filters=filters, connection=conn)
        await connection.execute(f'DELETE FROM "{table}" WHERE {where}', params)

    async def aggregate(self, table, *, filters=None, group=(), sum_field=None):
        columns(table)
        params = []
        group_columns = [field(table, name) for name in group]
        if sum_field:
            expression = f'json_build_object(%s::text,SUM({field(table, sum_field)})::text) AS "sum"'
            params.append(sum_field)
        else:
            expression = 'COUNT(*)::text AS "count"'
        where = predicate(table, filters, params)
        query = f'SELECT {",".join([*group_columns, expression])} FROM "{table}" WHERE {where}'
        if group_columns:
            query += " GROUP BY " + ",".join(group_columns)
        return normalize(await self.database.rows(query, params))
