import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal

from django.http import HttpRequest
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.names import PersonName, normalized_name
from club_api.core.schemas import Body
from club_api.core.views import api_view, parse_body
from club_api.domain import MAX_INTERESTS, achievement_progress, level_info
from club_api.modules.auth.service import require_alumni
from club_api.modules.gamification.routes import verified_alumni
from club_api.modules.members.mailing import mailing_consent
from club_api.modules.members.service import anonymize
from club_api.observability.audit import audit

ROUTE_LIMITS = {"/me/profile": 20, "/me/delete": 3}
FIO_LOCKED = "ФИО проверенного выпускника меняет офис клуба. Напишите в поддержку."
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def last_six_months(ledger, now=None):
    now = now or datetime.now(UTC)
    periods = [now.year * 12 + now.month - 1 - i for i in range(5, -1, -1)]
    buckets = {period: {"month": MONTHS[period % 12], "points": 0} for period in periods}
    for row in ledger:
        if row["created_at"] and row["delta"] > 0:
            date = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            key = date.year * 12 + date.month - 1
            if key in buckets:
                buckets[key]["points"] += row["delta"]
    return list(buckets.values())


class ProfileBody(Body):
    fio: PersonName = None
    contacts: dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(max_length=200)]] = None
    interests: list[Annotated[str, Field(max_length=80)]] = Field(default=None, max_length=30)
    mailing_consent: bool = None

    @field_validator("contacts")
    @classmethod
    def contacts_limit(cls, value):
        if len(value) > 12:
            raise ValueError("Слишком много контактов")
        return value


class DeleteBody(Body):
    confirm: Literal["УДАЛИТЬ"]


@api_view(permission=require_alumni)
async def me(request: HttpRequest):
    alumni = await require_alumni(request)
    state = request.services
    ledger, referred = await asyncio.gather(
        state.store.read(
            "points_ledger",
            filters={"alumni_id": {"_eq": alumni["id"]}},
            fields=("delta", "created_at", "reason"),
            limit=-1,
        ),
        state.store.read(
            "alumni", filters={"referred_by": {"_eq": alumni["id"]}}, fields=("verification_status",), limit=-1
        ),
    )
    verified = alumni["verification_status"] == "verified"
    level = level_info(alumni["points_cached"] or 0, alumni["personal_discount"] or 0 if verified else 0)
    if not verified:
        level["discount"] = 0
    stats = await state.gamification.stats(alumni, ledger) if verified else None
    if verified:
        await state.gamification.refresh_achievements(alumni["id"])
    profile = {
        key: alumni[key]
        for key in ("fio", "cohort", "verification_status", "edu_program", "edu_level", "avatar", "referral_code")
    }
    profile.update(
        contacts=alumni["contacts_json"] or {},
        interests=alumni["interests_json"] or [],
        referrals_verified=sum(row["verification_status"] == "verified" for row in referred),
        referrals_pending=sum(row["verification_status"] == "pending" for row in referred),
        mailing_consent=bool(alumni["mailing_consent_at"]),
    )
    result = {
        "alumni": profile,
        "level": level,
        "achievements": achievement_progress(stats) if stats else [],
        "activity": last_six_months(ledger) if verified else [],
    }
    return result


@api_view(body=ProfileBody)
async def profile(request: HttpRequest):
    alumni = await verified_alumni(request)
    body = parse_body(request, ProfileBody)
    data = body.model_dump(exclude_unset=True)
    patch = {}
    if "fio" in data and data["fio"] != normalized_name(alumni["fio"]):
        raise ApiError(409, FIO_LOCKED)
    if "contacts" in data:
        patch["contacts_json"] = data["contacts"]
    if "interests" in data:
        patch["interests_json"] = list(
            dict.fromkeys(value for value in data["interests"] if value in request.services.domain["legal_interests"])
        )[:MAX_INTERESTS]
    if "mailing_consent" in data and data["mailing_consent"] != bool(alumni["mailing_consent_at"]):
        patch.update(mailing_consent(data["mailing_consent"]))
        event = "mailing.consent" if data["mailing_consent"] else "mailing.withdraw"
        await audit(request, event, actor="alumni:" + alumni["id"], subject="alumni:" + alumni["id"])
    if patch:
        await request.services.store.update("alumni", patch, id=alumni["id"])
    return {"ok": True}


@api_view(body=DeleteBody, permission=require_alumni)
async def delete_me(request: HttpRequest):
    parse_body(request, DeleteBody)
    alumni = await require_alumni(request)
    await anonymize(request.services, alumni["id"])
    await audit(request, "alumni.self_delete", actor="alumni:" + alumni["id"], subject="alumni:" + alumni["id"])
    return {"ok": True}
