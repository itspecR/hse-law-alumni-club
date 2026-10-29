import asyncio
import json
import math
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from club_api.core.errors import ApiError
from club_api.core.outgoing import get_html

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
