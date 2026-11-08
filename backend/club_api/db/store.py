import re
from datetime import UTC, date, datetime
from uuid import UUID

from django.apps import apps
from django.db.models import Count, Field, Lookup, Q, Sum

from club_api.core.errors import ApiError
from club_api.db.schema import DATA_COLUMNS

BLOCK_FIELDS = frozenset(("blocks.collection", "blocks.sort", "blocks.item:block_hero.*", "blocks.item:block_cta.*"))
MEDIA_FIELDS = {
    "alumni": ("avatar",),
    "events": ("cover", "description"),
    "news": ("cover", "body"),
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


@Field.register_lookup
class InsensitiveContains(Lookup):
    lookup_name = "club_icontains"

    def as_sql(self, compiler, connection):
        lhs, params = self.process_lhs(compiler, connection)
        value = "%" + connection.ops.prep_for_like_query(self.rhs.lower()) + "%"
        return f"LOWER({lhs}) LIKE %s", [*params, value]


def model_for(table):
    columns(table)
    if table not in apps.all_models["club_data"]:
        for model in apps.get_app_config("club_data").get_models():
            if model._meta.db_table == table:
                return model
        raise invalid()
    return apps.all_models["club_data"][table]


def attributes(model):
    return {item.column: item.attname for item in model._meta.local_fields if item.column}


def condition(table, filters):
    predicate(table, filters, [])
    mapping = attributes(model_for(table))
    result = Q()
    for name, operators in (filters or {}).items():
        if name in ("_and", "_or"):
            parts = [condition(table, part) for part in operators]
            part = Q() if name == "_and" else Q(pk__in=[])
            for value in parts:
                part = part & value if name == "_and" else part | value
            result &= part
            continue
        name = mapping[name]
        for operator, value in operators.items():
            lookup = {
                "_eq": "exact",
                "_gt": "gt",
                "_gte": "gte",
                "_lt": "lt",
                "_lte": "lte",
                "_in": "in",
                "_contains": "contains",
                "_icontains": "club_icontains",
                "_starts_with": "startswith",
            }
            if operator == "_neq":
                part = (
                    Q(**{name + "__isnull": False})
                    if value is None
                    else ~Q(**{name: value}) & Q(**{name + "__isnull": False})
                )
            elif operator in ("_null", "_nnull"):
                part = Q(**{name + "__isnull": value if operator == "_null" else not value})
            elif operator == "_nin":
                part = (
                    Q(pk__in=[])
                    if None in value
                    else ~Q(**{name + "__in": value}) & (Q(**{name + "__isnull": False}) if value else Q())
                )
            else:
                part = Q(**{name + "__" + lookup[operator]: value})
            result &= part
    return result


def records(table, query, fields=None):
    selection(table, fields)
    mapping = attributes(model_for(table))
    names = (
        list(mapping.keys())
        if fields is None or "*" in fields
        else [name for name in fields if name not in BLOCK_FIELDS]
    )
    names = [name for name in names if name in DATA_COLUMNS[table]] or ["id"]
    return [{name: row[mapping[name]] for name in names} for row in query.values(*(mapping[name] for name in names))]


async def media_references(connection, table, data):
    ids = set()
    for name in MEDIA_FIELDS.get(table, ()):
        ids.update(media_ids(data.get(name)))
    if not ids:
        return
    await connection.lock("media.references")

    def check():
        model = apps.get_model("club_data", "MediaFile")
        files = list(model.objects.filter(pk__in=sorted(ids)).order_by("pk").select_for_update())
        if len(files) != len(ids):
            raise ApiError(400, "Один из файлов удален. Выберите файл заново.")
        if table != "alumni":
            avatars = set(model_for("alumni").objects.filter(avatar__in=ids).values_list("avatar", flat=True))
            if avatars or any((item.metadata or {}).get("club_upload_kind") == "avatar" for item in files):
                raise ApiError(
                    400, "Фотографию профиля нельзя использовать в материалах. Загрузите отдельный файл в медиатеку."
                )

    await connection.run(check)


class Store:
    def __init__(self, database):
        self.database = database

    async def read(self, table, *, filters=None, fields=None, sort=(), limit=100, offset=0, page=None, connection=None):
        selection(table, fields)
        predicate(table, filters, [])
        for name in sort:
            field(table, name.removeprefix("-"))
        if page is not None:
            offset = (page - 1) * (0 if limit == -1 else limit)
        if type(limit) is not int or limit < -1 or type(offset) is not int or offset < 0:
            raise invalid()
        if connection is None:
            async with self.database.connection() as conn:
                return await self.read(
                    table, filters=filters, fields=fields, sort=sort, limit=limit, offset=offset, connection=conn
                )

        def read_rows():
            model = model_for(table)
            mapping = attributes(model)
            query = model.objects.filter(condition(table, filters))
            if sort:
                query = query.order_by(
                    *(("-" if name.startswith("-") else "") + mapping[name.removeprefix("-")] for name in sort)
                )
            query = query[offset:] if limit == -1 else query[offset : offset + limit]
            blocks = table == "pages" and fields and any(name in BLOCK_FIELDS for name in fields)
            hidden_id = blocks and not any(name in ("id", "*") for name in fields)
            rows = records(table, query, [*fields, "id"] if hidden_id else fields)
            if blocks and rows:
                children = list(
                    model_for("pages_blocks")
                    .objects.filter(pages_id__in=[row["id"] for row in rows])
                    .order_by("sort", "pk")
                )
                for row in rows:
                    row["blocks"] = []
                    for child in children:
                        if child.pages_id != row["id"]:
                            continue
                        item = (
                            records(child.collection, model_for(child.collection).objects.filter(pk=child.item))
                            if child.collection in ("block_hero", "block_cta")
                            else []
                        )
                        row["blocks"].append(
                            {"collection": child.collection, "sort": child.sort, "item": item[0] if item else None}
                        )
                    if hidden_id:
                        del row["id"]
            return normalize(rows)

        return await connection.run(read_rows)

    async def one(self, table, id, *, fields=None, connection=None):
        rows = await self.read(table, filters={"id": {"_eq": id}}, fields=fields, limit=1, connection=connection)
        if not rows:
            raise ApiError(404, "Запись не найдена.")
        return rows[0]

    async def create(self, table, data, *, connection=None):
        columns(table)
        if table.startswith("directus_") or not isinstance(data, dict):
            raise invalid()
        for key in data:
            field(table, key)
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.create(table, data, connection=conn)
        await media_references(connection, table, data)

        def create_row():
            model = model_for(table)
            mapping = attributes(model)
            obj = model.objects.create(**{mapping[key]: value for key, value in data.items()})
            return normalize(records(table, model.objects.filter(pk=obj.pk))[0])

        return await connection.run(create_row)

    async def update(self, table, data, *, id=None, filters=None, connection=None):
        columns(table)
        if table.startswith("directus_") or not isinstance(data, dict) or "id" in data:
            raise invalid()
        if id is None and (not isinstance(filters, dict) or not filters):
            raise invalid()
        for key in data:
            field(table, key)
        predicate(table, {"id": {"_eq": id}} if id is not None else filters, [])
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.update(table, data, id=id, filters=filters, connection=conn)
        if not data:
            return await self.one(table, id, connection=connection) if id is not None else []
        await media_references(connection, table, data)

        def update_rows():
            model = model_for(table)
            mapping = attributes(model)
            query = model.objects.filter(condition(table, {"id": {"_eq": id}} if id is not None else filters))
            ids = list(query.select_for_update().values_list("pk", flat=True))
            query = model.objects.filter(pk__in=ids)
            query.update(**{mapping[key]: value for key, value in data.items()})
            rows = normalize(records(table, query))
            if id is not None and not rows:
                raise ApiError(404, "Запись не найдена.")
            return rows[0] if id is not None else rows

        return await connection.run(update_rows)

    async def delete(self, table, *, id=None, filters=None, connection=None):
        columns(table)
        if table.startswith("directus_") or (id is None and (not isinstance(filters, dict) or not filters)):
            raise invalid()
        predicate(table, {"id": {"_eq": id}} if id is not None else filters, [])
        if connection is None:
            async with self.database.transaction() as conn:
                return await self.delete(table, id=id, filters=filters, connection=conn)
        await connection.run(
            lambda: (
                model_for(table)
                .objects.filter(condition(table, {"id": {"_eq": id}} if id is not None else filters))
                .delete()
            )
        )

    async def aggregate(self, table, *, filters=None, group=(), sum_field=None):
        predicate(table, filters, [])
        for name in (*group, *((sum_field,) if sum_field else ())):
            field(table, name)

        def aggregate_rows():
            model = model_for(table)
            mapping = attributes(model)
            query = model.objects.filter(condition(table, filters))
            aggregate = Sum(mapping[sum_field]) if sum_field else Count("pk")
            key = "sum" if sum_field else "count"
            if group:
                rows = list(query.values(*(mapping[name] for name in group)).annotate(result=aggregate))
            else:
                rows = [query.aggregate(result=aggregate)]
            return normalize(
                [
                    {
                        **{name: row[mapping[name]] for name in group},
                        key: {sum_field: str(row["result"]) if row["result"] is not None else None}
                        if sum_field
                        else str(row["result"]),
                    }
                    for row in rows
                ]
            )

        async with self.database.connection() as connection:
            return await connection.run(aggregate_rows)
