from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.core.views import api_view
from club_api.modules.auth.service import require_admin
from club_api.modules.campaigns.store import CAMPAIGN_TABLE
from club_api.modules.donations.service import DONATION_TABLE, SUCCEEDED
from club_api.observability.audit import audit

CAMPAIGN_NOT_FOUND = "Сбор не найден."
LETTER_MISSING = "Заполните тему и текст письма-приглашения в блоке «Платеж и приглашение» и сохраните сбор."
LETTER_FIELDS = ("id", "slug", "invite_threshold", "invite_subject", "invite_body")
DONOR_FIELDS = ("number", "name", "email", "amount", "paid_at", "invited_at")


def letter_ready(campaign):
    return bool((campaign["invite_subject"] or "").strip() and (campaign["invite_body"] or "").strip())


async def invitation_campaign(state, id):
    rows = await state.store.read(CAMPAIGN_TABLE, filters={"id": {"_eq": guid(id)}}, fields=LETTER_FIELDS, limit=1)
    if not rows:
        raise ApiError(404, CAMPAIGN_NOT_FOUND)
    return rows[0]


@api_view(permission=require_admin)
async def donors(request: HttpRequest, id: str):
    await require_admin(request)
    state = request.services
    campaign = await invitation_campaign(state, id)
    eligible = {
        "campaign_id": {"_eq": campaign["id"]},
        "status": {"_eq": SUCCEEDED},
        "amount": {"_gte": campaign["invite_threshold"]},
    }
    return {
        "threshold": campaign["invite_threshold"],
        "letter_ready": letter_ready(campaign),
        "items": await state.store.read(
            DONATION_TABLE, filters=eligible, fields=DONOR_FIELDS, sort=("-paid_at", "number"), limit=-1
        ),
    }


@api_view(permission=require_admin)
async def invite(request: HttpRequest, id: str, number: str):
    admin = await require_admin(request)
    state = request.services
    campaign = await invitation_campaign(state, id)
    if not letter_ready(campaign):
        raise ApiError(409, LETTER_MISSING)
    await state.donations.invite(number, campaign)
    await audit(
        request,
        "donation.invited",
        actor="admin:" + admin["userId"],
        subject="donation:" + number,
        detail={"campaign": campaign["slug"]},
    )
    return {"ok": True}
