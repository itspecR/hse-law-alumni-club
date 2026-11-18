import re

from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.models import count, group_count, query_choice, query_page
from club_api.core.schemas import Body
from club_api.core.views import api_view, defer, parse_body
from club_api.modules.auth.service import require_admin, require_full_admin
from club_api.modules.checkout.admin import csv_response
from club_api.modules.office.analytics import analytics, analytics_rows, overview
from club_api.observability.audit import audit
from club_api.observability.health import build_health

AUDIT_PAGE_LIMIT = 50
AUDIT_READ_EVENTS = ("support.read",)
STAFF_ACTOR = "admin:"
AUDIT_EVENT = re.compile(r"(?=.{1,64}$)[a-z_]+(?:\.[a-z_]+)*")
AUDIT_STAFF = re.compile(r"admin:[0-9a-f-]{1,64}")
ROUTE_LIMITS = {"/admin/system-health": 12, ("POST", "/admin/push/broadcast"): 5}


class BroadcastBody(Body):
    title: str = Field(min_length=3, max_length=80)
    body: str = Field(min_length=3, max_length=200)
    url: str = Field(default="/", max_length=200, pattern="^/[a-zA-Z0-9/-]*$")


@api_view(permission=require_admin)
async def health(request: HttpRequest):
    await require_admin(request)
    return await build_health(request.services)


@api_view(permission=require_admin)
async def summary(request: HttpRequest):
    await require_admin(request)
    return await overview(request.services)


@api_view(permission=require_admin)
async def report(request: HttpRequest):
    await require_admin(request)
    return await analytics(request.services, query_choice(request, "range", ("7d", "30d", "90d")) or "30d")


@api_view(permission=require_admin)
async def export(request: HttpRequest):
    admin = await require_admin(request)
    range = query_choice(request, "range", ("7d", "30d", "90d")) or "30d"
    data = await analytics(request.services, range)
    await audit(request, "analytics.export", actor="admin:" + admin["userId"], detail={"range": range})
    return csv_response(analytics_rows(data), "analytics-" + range)


@api_view(body=BroadcastBody, permission=require_full_admin)
async def broadcast(request: HttpRequest):
    admin = await require_full_admin(request)
    body = parse_body(request, BroadcastBody)
    subscribers = await count(request.services.store, "push_subs")
    defer(request, request.services.push.to_all, body.model_dump())
    await audit(
        request, "push.broadcast", actor="admin:" + admin["userId"], detail={"title": body.title, "subs": subscribers}
    )
    return {"ok": True, "subscribers": subscribers}


@api_view(permission=require_full_admin)
async def audit_rows(request: HttpRequest):
    await require_full_admin(request)
    page, limit = query_page(request, default_limit=AUDIT_PAGE_LIMIT)
    filters = audit_filters(request)
    store = request.services.store
    rows = await store.read(
        "audit_log",
        filters=filters,
        sort=("-created_at", "id"),
        fields=("id", "event", "actor", "subject", "detail", "ip", "created_at"),
        limit=limit,
        offset=(page - 1) * limit,
    )
    return {
        "items": rows,
        "total": await count(store, "audit_log", filters),
        "page": page,
        "limit": limit,
        "staff": await audit_staff(store),
        "events": sorted(row["event"] for row in await group_count(store, "audit_log", ("event",))),
    }


def audit_value(request, name, pattern):
    value = request.GET.get(name) or None
    if value is not None and not pattern.fullmatch(value):
        raise ApiError(400, "Некорректный фильтр.")
    return value


def audit_filters(request):
    event = audit_value(request, "event", AUDIT_EVENT)
    actor = audit_value(request, "actor", AUDIT_STAFF)
    parts = [] if request.GET.get("reads") == "1" else [{"event": {"_nin": list(AUDIT_READ_EVENTS)}}]
    parts += [{"event": {"_eq": event}}] if event else []
    parts += [{"actor": {"_eq": actor}}] if actor else []
    return {"_and": parts} if parts else None


async def audit_staff(store):
    actors = await group_count(store, "audit_log", ("actor",), {"actor": {"_starts_with": STAFF_ACTOR}})
    ids = [row["actor"].removeprefix(STAFF_ACTOR) for row in actors]
    users = (
        await store.read("directus_users", filters={"id": {"_in": ids}}, fields=("id", "email"), limit=-1)
        if ids
        else []
    )
    emails = {str(user["id"]): user["email"] for user in users}
    return [
        {"actor": STAFF_ACTOR + id, "label": emails.get(id) or id}
        for id in sorted(ids, key=lambda id: emails.get(id) or id)
    ]
