import io
import logging
import re
from urllib.parse import urlsplit

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.models import RESERVED_SLUGS, slugify
from club_api.core.outgoing import IMAGE, Pacer, get_bytes, get_json, image_name
from club_api.core.views import api_view
from club_api.db.queries import Query, acquire_lock, release_lock
from club_api.domain import DPO_CATALOG
from club_api.modules.auth.service import require_admin
from club_api.modules.catalog.scope import DPO_PROGRAMS
from club_api.modules.catalog.sync import DOCUMENTS
from club_api.modules.media.service import MAX_IMAGE_BYTES
from club_api.observability.audit import audit

logger = logging.getLogger("club.dpo")
ROUTE_LIMITS = {("POST", "/admin/dpo-sync"): 3}
CATALOG_PATH = "/api/catalog/programs"
SYNC_LOCK = "dpo-sync"
SYNC_MINUTE = 41
MIN_PROGRAMS = 3
MIN_SHARE = 0.5
FAILURE_ALERT = 3
MAX_CATALOG_BYTES = 5_000_000
IMAGE_DELAY_SECONDS = 1
MEDIA_KIND = "dpo"
START_PREFIX = "Старт: "
PUBLISHED, ARCHIVED, OPEN = "published", "archived", "actual"
FORMATS = {"online": "online", "offline": "offline", "mixed": "blended", "hybrid": "blended"}
TEXT_LIMIT = 255
HSE_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
PAGE = re.compile(r"programs/[a-z0-9-]{1,60}(?:-[A-Za-z0-9_-]{1,32})?\.html")
MEDIA = re.compile(r"/media/[A-Za-z0-9._/-]{1,300}")
PRICE = re.compile(r"(\d[\d   ]{0,15})\s*₽")
NO_ADDRESS = "Адрес сайта ДПО не задан на сервере."
WRONG_ADDRESS = "Адрес сайта ДПО задан неверно: нужен https://домен без пути и порта."
NO_LIST = "Сайт ДПО вернул ответ без списка программ."
INCOMPLETE = "Сайт ДПО вернул неполный каталог. Список программ не изменен."
BUSY = "Каталог ДПО уже обновляется. Повторите через минуту."
FAILED = "Не удалось обновить каталог ДПО. Повторите позже."
PROGRAM_FIELDS = ("id", "slug", "status", "hse_id", "cover", "cover_source")


def site_base(state):
    address = state.settings.DPO_SITE_URL.strip()
    if not address:
        raise ApiError(409, NO_ADDRESS)
    try:
        parsed = urlsplit(address)
        port = parsed.port
    except ValueError:
        raise ApiError(502, WRONG_ADDRESS) from None
    extra = parsed.username or parsed.password or parsed.path.strip("/") or parsed.query or parsed.fragment
    if parsed.scheme != "https" or not parsed.hostname or port not in (None, 443) or extra:
        raise ApiError(502, WRONG_ADDRESS)
    return "https://" + parsed.hostname


def clean(value, limit=TEXT_LIMIT):
    return value.strip()[:limit] if isinstance(value, str) else ""


def parse_price(label):
    found = PRICE.fullmatch(clean(label))
    return int(re.sub(r"\D", "", found[1])) * 100 if found else 0


def start_dates(label):
    text = clean(label)
    start = text.removeprefix(START_PREFIX).strip() if text.startswith(START_PREFIX) else ""
    return {"start": start} if start else None


def cover_source(base, thumb):
    source = clean(thumb.get("src")) if isinstance(thumb, dict) else ""
    return base + source if MEDIA.fullmatch(source) else None


def map_card(card, base):
    if not isinstance(card, dict):
        return None
    hse_id, title, path = (clean(card.get(key)) for key in ("hse_id", "title", "path"))
    if not HSE_ID.fullmatch(hse_id) or not title or not PAGE.fullmatch(path):
        return None
    compare = card.get("compare") if isinstance(card.get("compare"), dict) else {}
    return {
        "hse_id": hse_id,
        "title": title,
        "direction": clean(card.get("sphere_title")) or None,
        "format": FORMATS.get(card.get("format")),
        "duration": clean(compare.get("duration")) or None,
        "dates": start_dates(card.get("start")),
        "price": parse_price(card.get("price")),
        "document": DOCUMENTS.get(clean(card.get("type_short"))),
        "source_url": f"{base}/{path}",
        "cover_source": cover_source(base, card.get("thumb")),
    }


def unique_cards(cards):
    found = {}
    for card in cards:
        if card:
            found.setdefault(card["hse_id"], card)
    return list(found.values())


async def fetch_cards(state, base):
    hosts = {urlsplit(base).hostname}
    payload = await get_json(state, base + CATALOG_PATH, allowed_hosts=hosts, max_bytes=MAX_CATALOG_BYTES)
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(cards, list):
        raise ApiError(502, NO_LIST)
    return unique_cards(map_card(card, base) for card in cards)


def check_complete(cards, published):
    if len(cards) < MIN_PROGRAMS or len(cards) < published * MIN_SHARE:
        raise ApiError(502, INCOMPLETE)


async def save_cover(state, pacer, url):
    try:
        await pacer.wait()
        data = await get_bytes(
            state, url, allowed_hosts={urlsplit(url).hostname}, max_bytes=MAX_IMAGE_BYTES, accept=IMAGE
        )
        return (await state.media.save(io.BytesIO(data), filename=image_name(url), kind=MEDIA_KIND))["id"]
    except ApiError:
        logger.warning("Обложка программы ДПО не загружена")
        return None


async def download_covers(state, cards, rows, covers):
    pacer = Pacer(IMAGE_DELAY_SECONDS)
    for card in cards:
        row = rows.get(card["hse_id"]) or {}
        if card["cover_source"] and card["cover_source"] != row.get("cover_source"):
            if saved := await save_cover(state, pacer, card["cover_source"]):
                covers[card["hse_id"]] = saved


def free_slug(card, taken):
    slug = slugify(card["title"])
    if slug in taken:
        slug = f"{slug}-{card['hse_id'].lower()}"
    candidate, number = slug, 2
    while candidate in taken:
        candidate, number = f"{slug}-{number}", number + 1
    taken.add(candidate)
    return candidate


def dropped_cover(card, row):
    return not card["cover_source"] and bool(row.get("cover_source"))


def program_data(card, row, covers):
    data = {key: value for key, value in card.items() if key != "cover_source"}
    data.update(status=PUBLISHED, enrollment=OPEN)
    if card["hse_id"] in covers:
        data.update(cover=covers[card["hse_id"]], cover_source=card["cover_source"])
    elif dropped_cover(card, row):
        data.update(cover=None, cover_source=None)
    return data


def plan(cards, rows, covers, taken):
    changes = []
    for card in cards:
        row = rows.get(card["hse_id"])
        data = program_data(card, row or {}, covers)
        if row:
            changes.append({"kind": "update", "id": row["id"], "data": data})
        else:
            changes.append({"kind": "create", "data": {**data, "slug": free_slug(card, taken), "catalog": DPO_CATALOG}})
    listed = {card["hse_id"] for card in cards}
    return changes + [
        {"kind": "archive", "id": row["id"], "data": {"status": ARCHIVED}}
        for key, row in rows.items()
        if key not in listed and row["status"] != ARCHIVED
    ]


def stale_covers(cards, rows, covers):
    stale = []
    for card in cards:
        row = rows.get(card["hse_id"]) or {}
        if row.get("cover") and row.get("cover_source") and (card["hse_id"] in covers or dropped_cover(card, row)):
            stale.append(row["cover"])
    return stale


async def dpo_rows(state):
    keyed = {}
    for row in await state.store.read("programs", filters=DPO_PROGRAMS, fields=PROGRAM_FIELDS, limit=-1):
        keyed[row["hse_id"] if row["hse_id"] and row["hse_id"] not in keyed else "row:" + str(row["id"])] = row
    return keyed


async def taken_slugs(state):
    rows = await state.store.read("programs", fields=("slug",), limit=-1)
    return {row["slug"] for row in rows if row["slug"]} | set(RESERVED_SLUGS.get("programs", ()))


async def apply(state, changes):
    async with state.database.transaction() as connection:
        for change in changes:
            if change["kind"] == "create":
                await state.store.create("programs", change["data"], connection=connection)
            else:
                await state.store.update("programs", change["data"], id=change["id"], connection=connection)


async def discard(state, ids):
    for id in ids:
        try:
            await state.media.delete(id)
        except ApiError:
            logger.warning("Старая обложка программы ДПО не удалена")


def summary(cards, changes):
    counts = {kind: sum(change["kind"] == kind for change in changes) for kind in ("create", "update", "archive")}
    return {
        "found": len(cards),
        "created": counts["create"],
        "updated": counts["update"],
        "archived": counts["archive"],
    }


async def load(state):
    base = site_base(state)
    cards, rows = await fetch_cards(state, base), await dpo_rows(state)
    check_complete(cards, sum(row["status"] == PUBLISHED for row in rows.values()))
    covers = {}
    try:
        await download_covers(state, cards, rows, covers)
        changes = plan(cards, rows, covers, await taken_slugs(state))
        await apply(state, changes)
    except Exception:
        await discard(state, covers.values())
        raise
    await discard(state, stale_covers(cards, rows, covers))
    return summary(cards, changes)


def alert(reason):
    return (
        f"Каталог ДПО не обновляется {FAILURE_ALERT} проверки подряд: {reason} "
        "На сайте клуба остаются программы последней удачной загрузки."
    )


async def record_success(state, result):
    await state.database.execute(
        Query(
            "INSERT INTO club_dpo_sync(id,found,created,updated,archived) VALUES(1,%s,%s,%s,%s) "
            "ON CONFLICT(id) DO UPDATE SET checked_at=now(),found=EXCLUDED.found,created=EXCLUDED.created,"
            "updated=EXCLUDED.updated,archived=EXCLUDED.archived,error=NULL,failures=0",
            "INSERT INTO club_dpo_sync(id,found,created,updated,archived) VALUES(1,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE checked_at=now(),found=VALUES(found),created=VALUES(created),"
            "updated=VALUES(updated),archived=VALUES(archived),error=NULL,failures=0",
        ),
        (result["found"], result["created"], result["updated"], result["archived"]),
    )


async def record_failure(state, reason):
    await state.database.execute(
        Query(
            "INSERT INTO club_dpo_sync(id,error,failures) VALUES(1,%s,1) ON CONFLICT(id) DO UPDATE "
            "SET checked_at=now(),error=EXCLUDED.error,failures=club_dpo_sync.failures+1",
            "INSERT INTO club_dpo_sync(id,error,failures) VALUES(1,%s,1) ON DUPLICATE KEY UPDATE "
            "checked_at=now(),error=VALUES(error),failures=failures+1",
        ),
        (reason,),
    )
    rows = await state.database.rows("SELECT failures FROM club_dpo_sync WHERE id=1")
    return rows[0]["failures"]


def failure(error):
    if isinstance(error, ApiError):
        return error.message
    logger.exception("Каталог ДПО не обновлен")
    return FAILED


async def refresh(state):
    try:
        result = await load(state)
    except Exception as error:
        reason = failure(error)
        logger.warning("Каталог ДПО не обновлен: %s", reason)
        if await record_failure(state, reason) == FAILURE_ALERT:
            await state.notifications.office_text(alert(reason))
        raise ApiError(502, reason) from None
    await record_success(state, result)
    return result


async def sync_center(state):
    async with state.database.connection() as connection:
        if not await acquire_lock(connection, SYNC_LOCK, wait=0):
            raise ApiError(409, BUSY)
        try:
            return await refresh(state)
        finally:
            await release_lock(connection, SYNC_LOCK)


def due(state, now):
    settings = state.settings
    return settings.DPO_SYNC_ENABLED == "true" and bool(settings.DPO_SITE_URL.strip()) and now.minute == SYNC_MINUTE


async def scheduled_sync(state):
    try:
        return await sync_center(state)
    except ApiError as error:
        return {"error": error.message}


@api_view(permission=require_admin)
async def sync(request: HttpRequest):
    admin = await require_admin(request)
    site_base(request.services)
    result = await sync_center(request.services)
    await audit(request, "catalog.dpo_sync", actor="admin:" + admin["userId"], detail=result)
    return {"ok": True, **result}
