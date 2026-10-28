import re
from typing import Literal

from django.http import HttpRequest
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.db.queries import Query
from club_api.db.store import normalize
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin
from club_api.modules.news.sources import SOURCES, import_candidate, refresh_source
from club_api.observability.audit import audit

ROUTE_LIMITS = {("POST", "/admin/news-sources/{source}/refresh"): 6}


class ImportBody(Body):
    title: str = Field(min_length=3, max_length=240)
    excerpt: str = Field(default="", max_length=2000)

    @field_validator("title", "excerpt", mode="before")
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value


class StateBody(Body):
    state: Literal["new", "dismissed"]


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
        Query(
            "SELECT * FROM club_news_inbox ORDER BY published_at DESC NULLS LAST,discovered_at DESC LIMIT 200",
            "SELECT * FROM club_news_inbox ORDER BY published_at DESC,discovered_at DESC LIMIT 200",
        )
    )
    return normalize(
        {
            "automatic": state.settings.NEWS_SYNC_ENABLED == "true",
            "sources": [{**source, **by_source.get(source["id"], {})} for source in SOURCES],
            "items": items,
        }
    )


@api_view(permission=require_admin)
async def refresh(request: HttpRequest, source: str):
    admin = await require_admin(request)
    result = await refresh_source(request.services, source)
    await audit(request, "news.source.refresh", actor="admin:" + admin["userId"], subject=source, detail=result)
    return result


@api_view(body=ImportBody, permission=require_admin)
async def import_news(request: HttpRequest, id: str):
    admin = await require_admin(request)
    body = parse_body(request, ImportBody)
    result = await import_candidate(request.services, candidate_id(id), body.title, body.excerpt)
    await audit(request, "news.source.import", actor="admin:" + admin["userId"], subject="news:" + result["id"])
    return result


@api_view(body=StateBody, permission=require_admin)
async def patch(request: HttpRequest, id: str):
    await require_admin(request)
    body = parse_body(request, StateBody)
    await request.services.database.execute(
        "UPDATE club_news_inbox SET state=%s WHERE id=%s AND state<>'imported'", (body.state, candidate_id(id))
    )
    return {"ok": True}
