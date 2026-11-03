from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view
from club_api.modules.auth.service import require_alumni


async def verified_alumni(request: HttpRequest):
    alumni = await require_alumni(request)
    if alumni["verification_status"] != "verified":
        raise ApiError(403, "Доступно после подтверждения статуса выпускника.")
    return alumni


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
