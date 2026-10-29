import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from club_web.formatting import FORMAT_LABELS, MONTHS, program_start, rub
from club_web.formatting import date as date_label

UPCOMING_LIMIT = 6
HOME_CLUB_PROGRAMS = 3


def moscow_today():
    return datetime.now(UTC).astimezone(ZoneInfo("Europe/Moscow")).date()


def start_day(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return instant.astimezone(ZoneInfo("Europe/Moscow")).date() if instant.tzinfo else instant.date()
    except ValueError:
        match = re.fullmatch(r"\s*(\d{1,2})\s+([а-я]+)\s+(\d{4})(?:\s*г\.?)?\s*", value.casefold())
        if not match or match[2] not in MONTHS:
            return None
        try:
            return date(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1]))
        except ValueError:
            return None


def prepare_catalog(items, params, today=None):
    today = today or moscow_today()
    programs = []
    for item in items:
        start = (item.get("dates") or {}).get("start") or ""
        day = start_day(start)
        programs.append(
            {
                **item,
                "id": str(item.get("id") or item["slug"]),
                "document": item.get("document") or "",
                "start_label": date_label(start),
                "start_day": day.isoformat() if day else "",
                "price_label": rub(item["price"]) if item.get("price") is not None else "Стоимость уточняется",
                "format_label": FORMAT_LABELS.get(item.get("format"), item.get("format") or "Не указан"),
            }
        )
    fields = ("direction", "format", "document", "enrollment")
    selected = [
        item
        for item in programs
        if all(not params.get(field) or item.get(field) == params[field] for field in fields)
        and params.get("q", "").casefold() in item.get("title", "").casefold()
    ]
    order = params.get("sort", "title")
    selected.sort(
        key=lambda item: (
            (item.get("price") is None, (item.get("price") or 0) * (-1 if order == "price-desc" else 1))
            if order in ("price", "price-desc")
            else item.get("title", "").casefold()
        )
    )
    upcoming = sorted(
        (item for item in programs if item["start_day"] >= today.isoformat() and item.get("enrollment") == "actual"),
        key=lambda item: (item["start_day"], item["title"].casefold()),
    )[:UPCOMING_LIMIT]
    public_fields = (
        "id",
        "slug",
        "title",
        "direction",
        "format_label",
        "duration",
        "document",
        "start_label",
        "price_label",
        "enrollment",
    )
    return {
        "items": selected,
        "filters": {field: sorted({str(item.get(field) or "") for item in programs} - {""}) for field in fields},
        "upcoming": upcoming,
        "program_data": [{key: item.get(key) or "" for key in public_fields} for item in programs],
    }


def preview_order(item, today):
    day = start_day(program_start(item))
    upcoming = day is not None and day >= today
    return item.get("enrollment") == "nonactual", not upcoming, day if upcoming else date.max


def club_programs_preview(items, today=None):
    today = today or moscow_today()
    return sorted(items, key=lambda item: preview_order(item, today))[:HOME_CLUB_PROGRAMS]
