import re
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

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
FORMAT_LABELS = {"online": "онлайн", "offline": "очно", "blended": "смешанный"}
LEADING_SYMBOLS = re.compile(r"^[^\w«\"№(]+")


def plain_title(value):
    return LEADING_SYMBOLS.sub("", value or "")


def rub(value):
    amount = (value or 0) / 100
    formatted = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return formatted.replace(",", "\u00a0").replace(".", ",") + " ₽"


def open_cohorts(item):
    cohorts = item.get("cohorts")
    return cohorts if isinstance(cohorts, list) else []


def program_start(item):
    cohorts = open_cohorts(item)
    return cohorts[0].get("starts_on") if cohorts else (item.get("dates") or {}).get("start")


def program_price(item):
    prices = sorted({cohort.get("price") or 0 for cohort in open_cohorts(item)}) or [item.get("price")]
    return ("от " if len(prices) > 1 else "") + rub(prices[0])


def discount_label(order, automatic=False):
    if order.get("office_discount") is not None and not automatic:
        return f"скидка учебного офиса: {order['office_discount']}%"
    percent = order.get("member_discount") or 0
    if order.get("discount_source") == "promo":
        return f"промокод {order.get('promo_code') or ''}: {percent}%"
    return f"скидка выпускника: {percent}%" if percent else "без скидки"


def date(value, time=False):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(ZoneInfo("Europe/Moscow"))
        result = f"{parsed.day} {MONTHS[parsed.month - 1]} {parsed.year}"
        return result + (parsed.strftime(" · %H:%M") if time else "")
    except ValueError, TypeError:
        return str(value)


def safe_url(value):
    if not isinstance(value, str) or re.search(r"[\x00-\x20\\]", value):
        return ""
    if value.startswith("/") and not value.startswith("//"):
        return value
    try:
        parts = urlsplit(value)
        _ = parts.port
        if parts.scheme in ("http", "https") and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return ""


def payment_url(value):
    url = safe_url(value)
    parts = urlsplit(url)
    return url if parts.scheme == "https" else ""


def media(value):
    if not value:
        return ""
    if re.fullmatch(r"[0-9a-fA-F-]{36}", value):
        return "/api/media/" + value
    if re.fullmatch(r"/assets/[0-9a-fA-F-]{36}", value):
        return "/api/media/" + value.rsplit("/", 1)[1]
    return safe_url(value)


def page_items(value):
    return value.get("items", []) if isinstance(value, dict) else value or []
