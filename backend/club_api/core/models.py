import copy
import re
from datetime import UTC, datetime
from uuid import UUID

from pydantic import create_model

from club_api.core.errors import ApiError


def partial(model):
    fields = {}
    for name, info in model.model_fields.items():
        optional = copy.deepcopy(info)
        optional.default, optional.default_factory = (None, None)
        fields[name] = (info.annotation, optional)
    return create_model(model.__name__ + "Patch", __base__=model, **fields)


def guid(value):
    try:
        if not isinstance(value, str) or len(value) != 36 or str(UUID(value)) != value.lower():
            raise ValueError
        return value
    except ValueError, AttributeError:
        raise ApiError(400, "Некорректный идентификатор") from None


def query_page(request, *, default_limit=50):
    try:
        page = int(request.GET.get("page", "1"))
        limit = int(request.GET.get("limit", str(default_limit)))
        if not 1 <= page <= 1000000 or not 1 <= limit <= 100:
            raise ValueError
        return (page, limit)
    except ValueError:
        raise ApiError(400, "Некорректные параметры страницы") from None


def query_choice(request, name, choices):
    value = request.GET.get(name) or None
    if value is not None and value not in choices:
        raise ApiError(400, "Некорректный фильтр")
    return value


def query_search(request):
    value = request.GET.get("q", "")
    if len(value) > 100:
        raise ApiError(400, "Слишком длинный поисковый запрос")
    return value.strip()


def sub_active(until):
    if not until:
        return False
    return parse_date(until) > datetime.now(UTC)


def parse_date(value):
    value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def count(store, table, filters=None):
    rows = await store.aggregate(table, filters=filters)
    return int(rows[0]["count"]) if rows else 0


async def group_count(store, table, group, filters=None):
    return [{**row, "count": int(row["count"])} for row in await store.aggregate(table, filters=filters, group=group)]


def slugify(value):
    alphabet = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
    latin = [
        "a",
        "b",
        "v",
        "g",
        "d",
        "e",
        "e",
        "zh",
        "z",
        "i",
        "j",
        "k",
        "l",
        "m",
        "n",
        "o",
        "p",
        "r",
        "s",
        "t",
        "u",
        "f",
        "h",
        "c",
        "ch",
        "sh",
        "sch",
        "",
        "y",
        "",
        "e",
        "yu",
        "ya",
    ]
    translated = value.lower().translate(str.maketrans(dict(zip(alphabet, latin, strict=True))))
    slug = re.sub("[^a-z0-9]+", "-", translated).strip("-")[:60]
    if slug:
        return slug
    hashed = 0
    encoded = value.encode("utf-16-le", errors="surrogatepass")
    for offset in range(0, len(encoded), 2):
        hashed = hashed * 31 + int.from_bytes(encoded[offset : offset + 2], "little") & 4294967295
    return f"item-{abs(hashed if hashed < 2**31 else hashed - 2**32)}"


async def unique_slug(store, table, title, connection=None):
    slug = slugify(title)
    if await store.read(table, filters={"slug": {"_eq": slug}}, fields=("id",), limit=1, connection=connection):
        import secrets

        return slug + "-" + secrets.token_hex(4)
    return slug
