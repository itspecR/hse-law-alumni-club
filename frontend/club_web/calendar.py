import calendar
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def instant(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if not parsed.tzinfo else parsed.astimezone(UTC)
    except ValueError, TypeError, AttributeError:
        return datetime.min.replace(tzinfo=UTC)


def google_calendar(event):
    start = instant(event.get("starts_at"))
    if start.year < 1900:
        return ""
    dates = "/".join(value.strftime("%Y%m%dT%H%M%SZ") for value in (start, start + timedelta(hours=2)))
    fields = {"action": "TEMPLATE", "text": event["title"], "dates": dates, "details": event.get("description") or ""}
    if event.get("reg_url"):
        fields["details"] += "\nРегистрация: " + event["reg_url"]
    if event.get("location") and event.get("format") != "online":
        fields["location"] = event["location"]
    return "https://calendar.google.com/calendar/render?" + urlencode(fields)


def calendar_file(event, public_url, now=None):
    start = instant(event.get("starts_at"))
    if start.year < 1900:
        raise ValueError("Событие не содержит корректной даты")

    def escape(value):
        return (
            str(value or "")
            .replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\n")
        )

    def stamp(value):
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Клуб выпускников факультета права НИУ ВШЭ//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:event-{event['id']}@club-pravo-hse",
        "DTSTAMP:" + stamp(now or datetime.now(UTC)),
        "DTSTART:" + stamp(start),
        "DTEND:" + stamp(start + timedelta(hours=2)),
        "SUMMARY:" + escape(event["title"]),
        "DESCRIPTION:" + escape(event.get("description")),
        "URL:" + public_url.rstrip("/") + "/events/" + event["id"],
    ]
    if event.get("location") and event.get("format") != "online":
        lines.append("LOCATION:" + escape(event["location"]))
    lines.extend(["END:VEVENT", "END:VCALENDAR"])
    folded = []
    for line in lines:
        part = ""
        for character in line:
            if len((part + character).encode()) > 75:
                folded.append(part)
                part = " "
            part += character
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"


def month_view(events, selected, now=None):
    now = (now or datetime.now(UTC)).astimezone(ZoneInfo("Europe/Moscow"))
    year, month = now.year, now.month
    if re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", selected or ""):
        year, month = map(int, selected.split("-"))
    grouped = {}
    for event in events:
        day = instant(event.get("starts_at")).astimezone(ZoneInfo("Europe/Moscow"))
        if (day.year, day.month) == (year, month):
            grouped.setdefault(day.day, []).append(event)
    previous = datetime(year, month, 1) - timedelta(days=1)
    following = datetime(year + (month == 12), month % 12 + 1, 1)
    return {
        "title": f"{MONTHS[month - 1]} {year}",
        "selected": f"{year:04}-{month:02}",
        "previous": previous.strftime("%Y-%m"),
        "next": following.strftime("%Y-%m"),
        "weeks": [
            [{"day": day, "events": grouped.get(day, [])} for day in week]
            for week in calendar.monthcalendar(year, month)
        ],
    }
