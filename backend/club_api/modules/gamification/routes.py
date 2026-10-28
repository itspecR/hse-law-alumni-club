from typing import Literal
from uuid import UUID

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_alumni
from club_api.observability.audit import audit


class PointsBody(Body):
    alumni_id: str
    reason: Literal["program", "event", "referral", "mentorship", "order", "decay", "manual", "achievement"]
    delta: int | None = None
    ref: str | None = None
    comment: str | None = None
    idempotency_key: str | None = None


async def verified_alumni(request: HttpRequest):
    alumni = await require_alumni(request)
    if alumni["verification_status"] != "verified":
        raise ApiError(403, "Доступно после подтверждения статуса выпускника.")
    return alumni


@api_view
async def points(request: HttpRequest):
    body = parse_body(request, PointsBody)
    state = request.services
    if not state.auth.service_token(request):
        raise ApiError(401, "Требуется сервисный токен.")
    try:
        UUID(body.alumni_id)
    except ValueError as error:
        raise ApiError(400, "Некорректный участник.") from error
    result = await state.gamification.add(**body.model_dump())
    await audit(
        request,
        "points.service",
        actor="service",
        subject="alumni:" + body.alumni_id,
        detail={"reason": body.reason, "delta": body.delta},
    )
    return {"ok": True, **result}


@api_view
async def decay(request: HttpRequest):
    if not request.services.auth.service_token(request):
        raise ApiError(401, "Требуется сервисный токен.")
    return await request.services.gamification.decay()


@api_view
async def ledger(request: HttpRequest):
    alumni = await verified_alumni(request)
    return await request.services.store.read(
        "points_ledger",
        filters={"alumni_id": {"_eq": alumni["id"]}},
        fields=("id", "delta", "reason", "ref", "comment", "created_at"),
        sort=("-created_at",),
        limit=50,
    )
