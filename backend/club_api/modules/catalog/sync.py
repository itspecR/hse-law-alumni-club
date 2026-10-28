import asyncio
import json
import math
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.models import slugify
from club_api.core.outgoing import get_html
from club_api.core.views import api_view
from club_api.db.queries import acquire_lock
from club_api.modules.auth.service import require_admin
from club_api.observability.audit import audit

ROUTE_LIMITS = {("POST", "/admin/dpo-sync"): 3}
ACTUAL_URL = "https://www.hse.ru/edu/dpo/?orgUnit=22753"
ALL_URL = "https://www.hse.ru/edu/dpo/?onlyActual=0&orgUnit=22753"
DPO_PAGE_LIMIT = 20
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
MONTH_NAMES = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
DOCUMENTS = {
    "ПК": "Удостоверение о повышении квалификации НИУ ВШЭ",
    "ПП": "Диплом о профессиональной переподготовке НИУ ВШЭ",
}


def state_json(source):
    tokens, index = ([], 0)
    while index < len(source):
        char = source[index]
        if char == '"':
            match = re.compile('"(?:\\\\.|[^"\\\\])*"').match(source, index)
            if not match:
                raise ValueError("Некорректная строка")
            tokens.append(match[0])
            index += len(match[0])
            continue
        if re.match("[A-Za-z_$]", char):
            word = re.compile("[A-Za-z0-9_$]+").match(source, index)[0]
            end = index + len(word)
            if word == "new":
                match = re.compile("\\s*Date\\s*\\(\\s*([0-9]+)\\s*\\)").match(source, end)
                if match:
                    tokens.append(match[1])
                    index = end + len(match[0])
                    continue
            if word == "__proto__":
                match = re.compile("\\s*:\\s*null\\b").match(source, end)
                if match:
                    index = end + len(match[0])
                    previous = len(tokens) - 1
                    while previous >= 0 and tokens[previous].isspace():
                        previous -= 1
                    if previous >= 0 and tokens[previous] == ",":
                        tokens = tokens[:previous]
                    else:
                        comma = re.compile("\\s*,").match(source, index)
                        if comma:
                            index += len(comma[0])
                    continue
            following = end
            while following < len(source) and source[following].isspace():
                following += 1
            tokens.append(json.dumps(word) if following < len(source) and source[following] == ":" else word)
            index = end
            continue
        tokens.append(char)
        index += 1
    return json.loads("".join(tokens))


def parse_initial_state(html):
    match = re.search("window\\.__INITIAL_STATE__\\s*=\\s*(\\{[\\s\\S]*?\\});\\s*window\\.__URQL_DATA__", html)
    if not match:
        raise ApiError(502, "Разметка каталога изменилась. Обновление отменено.")
    try:
        state = state_json(match[1])
        items, total, page_size = state["items"], state["total"], state["pageSize"]
        if not isinstance(items, list) or type(total) is not int or type(page_size) is not int:
            raise ValueError
        return {
            "items": items,
            "total": total,
            "page_size": page_size,
        }
    except ValueError, TypeError, KeyError, AttributeError, RecursionError:
        raise ApiError(502, "Не удалось прочитать каталог. Обновление отменено.") from None


def label(value):
    return (
        str(value.get("shortTitle") or value.get("title") or "").strip()
        if isinstance(value, dict)
        else str(value or "").strip()
    )


def map_item(item):
    if not isinstance(item, dict):
        return None
    id, title = (str(item.get("id") or "").strip(), str(item.get("title") or "").strip())
    if not re.fullmatch("[0-9]+", id) or not title:
        return None
    url = str(item.get("url") or "").strip()
    if not re.match("https://(?:[a-z0-9-]+\\.)*hse\\.ru(?:[/?#]|$)", url, re.I):
        url = "https://www.hse.ru/edu/dpo/" + id
    format_raw = label(item.get("studyFormat"))
    format = (
        "online"
        if (format_raw or "Онлайн").lower().startswith("онлайн")
        else "offline"
        if format_raw.lower().startswith("очн")
        else "blended"
    )
    price = item.get("discountPrice") if item.get("discountPrice") is not None else item.get("educationPricing")
    price_kop = (
        math.floor(price + 0.5) * 100 if type(price) in (int, float) and math.isfinite(price) and (price >= 0) else 0
    )
    if price_kop > 9007199254740991:
        raise ApiError(502, "Цена в источнике превышает допустимый размер.")
    start = None
    if item.get("startDate") is not None:
        try:
            date = datetime.fromtimestamp(float(item["startDate"]) / 1000, UTC).astimezone(ZoneInfo("Europe/Moscow"))
            start = (
                f"{date.day} {MONTHS[date.month - 1]} {date.year} г."
                if not item.get("isStartDateWithoutDay")
                else f"{MONTH_NAMES[date.month - 1]} {date.year} г."
            )
        except ValueError, OverflowError, OSError:
            start = None
    return {
        "hseId": id,
        "url": url,
        "title": title,
        "category": label(item.get("sphere")) or label(item.get("orgUnit")) or "Право",
        "type": "ПП" if "пп" in label(item.get("type")).lower() else "ПК",
        "formatRaw": format_raw,
        "format": format,
        "start": start,
        "duration": str(item.get("hours") or item.get("duration") or "").strip() or None,
        "priceKop": price_kop,
    }


def page_url(base, page):
    url = urlsplit(base)
    params = [(key, value) for key, value in parse_qsl(url.query) if key != "page"]
    if not any((key == "orgUnit" for key, _ in params)):
        params.append(("orgUnit", "22753"))
    if page > 1:
        params.append(("page", str(page)))
    return urlunsplit((url.scheme, url.netloc, url.path, urlencode(params), url.fragment))


async def collect(state, base):

    async def fetch(page):
        html = await get_html(state, page_url(base, page), allowed_hosts={"www.hse.ru", "hse.ru"}, max_bytes=8000000)
        return await asyncio.to_thread(parse_initial_state, html)

    first = await fetch(1)
    if first["page_size"] <= 0 or first["total"] < 0 or first["total"] > first["page_size"] * DPO_PAGE_LIMIT:
        raise ApiError(502, "Каталог превышает предел страниц. Обновление отменено.")
    cards = {}
    for page in range(1, max(1, math.ceil(first["total"] / first["page_size"])) + 1):
        current = first if page == 1 else await fetch(page)
        expected = min(first["page_size"], first["total"] - (page - 1) * first["page_size"])
        if (
            current["total"] != first["total"]
            or current["page_size"] != first["page_size"]
            or len(current["items"]) != expected
        ):
            raise ApiError(502, "Источник вернул неполный каталог. Обновление отменено.")
        for item in current["items"]:
            card = map_item(item)
            if not card or card["hseId"] in cards:
                raise ApiError(502, "Источник вернул неполный каталог. Обновление отменено.")
            cards[card["hseId"]] = card
    return list(cards.values())


def normalized_title(title):
    return re.sub(
        "\\s+", " ", re.sub("\\s*\\([^)]*\\)\\s*$", "", title.split(" / ")[0]).lower().replace("ё", "е")
    ).strip()


def plan_sync(cards, existing):
    by_id, by_title, managed = ({}, {}, set())
    for row in existing:
        source = re.match("https://(?:www\\.)?hse\\.ru/edu/dpo/([0-9]+)", row["source_url"] or "")
        if re.fullmatch("[0-9]+", row.get("hse_id") or ""):
            by_id[row["hse_id"]] = row
            managed.add(row["id"])
        if source:
            by_id[source[1]] = row
            by_title[normalized_title(row["title"])] = row
            managed.add(row["id"])
    slugs, matched, changes = ({row["slug"] for row in existing}, set(), [])
    for card in cards:
        row = by_id.get(card["hseId"]) or by_title.get(normalized_title(card["title"]))
        patch = {
            "format": card["format"],
            "document": DOCUMENTS[card["type"]],
            "source_url": card["url"],
            "hse_id": card["hseId"],
            "enrollment": card["enrollment"],
        }
        if card["priceKop"] > 0:
            patch["price"] = card["priceKop"]
        if card["start"]:
            patch["dates"] = {"start": card["start"]}
        if row and row["id"] not in matched:
            matched.add(row["id"])
            patch["status"] = "published" if card["priceKop"] > 0 or row["price"] > 0 else "draft"
            if card["duration"]:
                patch["duration"] = card["duration"]
            changes.append({"kind": "update", "id": row["id"], "data": patch})
        elif not row:
            slug = slugify(card["title"].split(" / ")[0])
            while slug in slugs:
                slug += "-" + card["hseId"][-4:]
            slugs.add(slug)
            changes.append(
                {
                    "kind": "create",
                    "data": {
                        **patch,
                        "slug": slug,
                        "title": card["title"].split(" / ")[0].strip(),
                        "direction": card["category"] or "Право",
                        "duration": card["duration"] or "уточняется",
                        "price": card["priceKop"],
                        "dates": {"start": card["start"]} if card["start"] else None,
                        "description": None,
                        "status": "published" if card["priceKop"] > 0 else "draft",
                    },
                }
            )
    changes.extend(
        {"kind": "archive", "id": row["id"], "data": {"status": "archived"}}
        for row in existing
        if row["id"] in managed and row["id"] not in matched and (row["status"] != "archived")
    )
    return changes


async def sync_catalog(state):
    actual_url, all_url = (state.settings.HSE_DPO_URL or ACTUAL_URL, state.settings.HSE_DPO_ALL_URL or ALL_URL)
    actual, all_cards = (await collect(state, actual_url), await collect(state, all_url))
    if len(actual) < 3 or len(all_cards) < len(actual):
        raise ApiError(502, "Источник вернул неполный каталог. Обновление отменено.")
    actual_ids = {card["hseId"] for card in actual}
    if not actual_ids.issubset({card["hseId"] for card in all_cards}):
        raise ApiError(502, "Списки программ в источнике не совпадают. Обновление отменено.")
    cards = {
        card["hseId"]: {**card, "enrollment": "actual" if card["hseId"] in actual_ids else "nonactual"}
        for card in all_cards
    }
    for card in actual:
        cards.setdefault(card["hseId"], {**card, "enrollment": "actual"})
    result = {
        "created": 0,
        "updated": 0,
        "archived": 0,
        "actual": len(actual_ids),
        "nonactual": sum(card["enrollment"] == "nonactual" for card in cards.values()),
        "total": len(cards),
        "sources": {"actual": actual_url, "all": all_url},
    }
    async with state.database.transaction() as connection:
        await acquire_lock(connection, "dpo-sync")
        existing = await state.store.read(
            "programs",
            fields=("id", "slug", "title", "status", "source_url", "duration", "price", "hse_id"),
            limit=-1,
            connection=connection,
        )
        for change in plan_sync(list(cards.values()), existing):
            if change["kind"] == "create":
                await state.store.create("programs", change["data"], connection=connection)
            else:
                await state.store.update("programs", change["data"], id=change["id"], connection=connection)
            result[{"create": "created", "update": "updated", "archive": "archived"}[change["kind"]]] += 1
    return result


@api_view(permission=require_admin)
async def sync(request: HttpRequest):
    admin = await require_admin(request)
    result = await sync_catalog(request.services)
    await audit(request, "catalog.dpo_sync", actor="admin:" + admin["userId"], detail=result)
    return {"ok": True, **result}
