from datetime import date, datetime
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def moscow_today():
    return datetime.now(MOSCOW).date()


def iso_date(value):
    return date.fromisoformat(value).isoformat()


def display_date(value):
    return date.fromisoformat(value).strftime("%d.%m.%Y")
