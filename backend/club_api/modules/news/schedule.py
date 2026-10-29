from datetime import datetime, timedelta

from club_api.core.models import CLUB_TIMEZONE
from club_api.db.queries import Query
from club_api.modules.news.importer import sync_news

INTERVALS = (1, 3, 6, 12, 24)
DEFAULT_INTERVAL = 3
CHECK_MINUTE = 17
SETTINGS_ID = 1
DAY_HOURS = 24


async def news_settings(state):
    rows = await state.database.rows(
        "SELECT enabled,interval_hours FROM club_news_settings WHERE id=%s", (SETTINGS_ID,)
    )
    row = rows[0] if rows else {}
    return {"enabled": bool(row.get("enabled")), "interval_hours": row.get("interval_hours") or DEFAULT_INTERVAL}


async def save_settings(state, enabled, interval):
    await state.database.execute(
        Query(
            "INSERT INTO club_news_settings(id,enabled,interval_hours) VALUES(%s,%s,%s) ON CONFLICT(id) DO UPDATE "
            "SET enabled=EXCLUDED.enabled,interval_hours=EXCLUDED.interval_hours,updated_at=now()",
            "INSERT INTO club_news_settings(id,enabled,interval_hours) VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "enabled=VALUES(enabled),interval_hours=VALUES(interval_hours),updated_at=now()",
        ),
        (SETTINGS_ID, enabled, interval),
    )


def on_schedule(moment, interval):
    return moment.hour % interval == 0


def next_check(moment, interval):
    start = moment.astimezone(CLUB_TIMEZONE).replace(minute=CHECK_MINUTE, second=0, microsecond=0)
    candidates = (start + timedelta(hours=hours) for hours in range(DAY_HOURS + 1))
    return next(candidate for candidate in candidates if candidate > moment and on_schedule(candidate, interval))


async def scheduled_news(state, moment=None):
    moment = moment or datetime.now(CLUB_TIMEZONE)
    settings = await news_settings(state)
    if settings["enabled"] and on_schedule(moment, settings["interval_hours"]):
        return await sync_news(state)
    return None
