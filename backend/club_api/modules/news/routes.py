import re
from datetime import UTC, datetime
from typing import Literal

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.db.store import normalize
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin
from club_api.modules.news.importer import SYNC_LOCK, sync_news
from club_api.modules.news.schedule import INTERVALS, news_settings, next_check, save_settings
from club_api.modules.news.sources import SOURCE_IDS, SOURCES
from club_api.observability.audit import audit

ROUTE_LIMITS = {("POST", "/admin/news-sources/refresh"): 6}
INBOX_LIMIT = 200


class StateBody(Body):
    state: Literal["new", "dismissed"]


class SettingsBody(Body):
    enabled: bool
    interval_hours: Literal[tuple(str(hours) for hours in INTERVALS)]


async def schedule(state):
    settings = await news_settings(state)
    allowed = state.settings.NEWS_SYNC_ENABLED == "true"
    upcoming = next_check(datetime.now(UTC), settings["interval_hours"]) if allowed and settings["enabled"] else None
    return {**settings, "allowed": allowed, "next_check": upcoming, "intervals": INTERVALS}


def current(item):
    return item["state"] == "imported" or bool(SOURCE_IDS & set(item["sources"] or ()))


def candidate_id(value):
    if not re.fullmatch("[a-f0-9]{64}", value):
        raise ApiError(400, "Некорректный идентификатор.")
    return value


@api_view(permission=require_admin)
async def sources(request: HttpRequest):
    await require_admin(request)
    state = request.services
    runs = await state.database.rows("SELECT * FROM club_news_source_runs")
    by_source = {row["source"]: row for row in runs}
    items = await state.database.rows(
        "SELECT i.id,i.source_url,i.title,i.state,i.sources,i.discovered_at,n.slug AS news_slug,n.status AS news_status,"
        "COALESCE(n.published_at,i.published_at) AS published_at FROM club_news_inbox i LEFT JOIN news n ON n.id=i.news_id "
        "ORDER BY COALESCE(n.published_at,i.published_at,i.discovered_at) DESC,i.id LIMIT %s",
        (INBOX_LIMIT,),
    )
    return normalize(
        {
            "schedule": await schedule(state),
            "sources": [{**source, **by_source.get(source["id"], {})} for source in SOURCES],
            "items": [item for item in items if current(item)],
        }
    )


@api_view(permission=require_admin)
async def refresh(request: HttpRequest):
    admin = await require_admin(request)
    state = request.services
    started = state.jobs.run(SYNC_LOCK, lambda: sync_news(state)) is not None
    await audit(request, "news.source.refresh", actor="admin:" + admin["userId"], detail={"started": started})
    return {"started": started}


@api_view(body=SettingsBody, permission=require_admin)
async def settings(request: HttpRequest):
    admin = await require_admin(request)
    body = parse_body(request, SettingsBody)
    await save_settings(request.services, body.enabled, int(body.interval_hours))
    detail = {"enabled": body.enabled, "interval_hours": int(body.interval_hours)}
    await audit(request, "news.settings", actor="admin:" + admin["userId"], detail=detail)
    return normalize(await schedule(request.services))


@api_view(body=StateBody, permission=require_admin)
async def patch(request: HttpRequest, id: str):
    await require_admin(request)
    body = parse_body(request, StateBody)
    await request.services.database.execute(
        "UPDATE club_news_inbox SET state=%s WHERE id=%s AND state<>'imported'", (body.state, candidate_id(id))
    )
    return {"ok": True}
