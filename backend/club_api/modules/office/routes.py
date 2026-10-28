from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.models import count, query_choice
from club_api.core.views import api_view, defer, parse_body
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin, require_full_admin
from club_api.modules.checkout.admin import csv_response
from club_api.modules.office.analytics import analytics, analytics_rows, overview
from club_api.observability.audit import audit
from club_api.observability.health import build_health

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


@api_view(permission=require_admin)
async def audit_rows(request: HttpRequest):
    await require_admin(request)
    try:
        limit = int(request.GET.get("limit", "300"))
        if not 1 <= limit <= 500:
            raise ValueError
    except ValueError:
        raise ApiError(400, "Некорректный размер списка.") from None
    return await request.services.store.read(
        "audit_log",
        sort=("-created_at",),
        fields=("id", "event", "actor", "subject", "detail", "ip", "created_at"),
        limit=limit,
    )
