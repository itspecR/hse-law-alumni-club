import asyncio
from datetime import UTC, datetime
from typing import Annotated, Literal

from django.http import HttpRequest, JsonResponse
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.domain import MAX_INTERESTS, achievement_progress, level_info
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_alumni
from club_api.modules.gamification.routes import verified_alumni
from club_api.modules.gamification.social import social_progress
from club_api.modules.members.service import anonymize
from club_api.modules.telegram.links import make_link
from club_api.observability.audit import audit

ROUTE_LIMITS = {"/me/tg-link": 10, "/me/profile": 20, "/me/export": 5, "/me/delete": 3}
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
    fio: str = Field(default=None, min_length=2, max_length=200)
    contacts: dict[Annotated[str, Field(max_length=40)], Annotated[str, Field(max_length=200)]] = None
    interests: list[Annotated[str, Field(max_length=80)]] = Field(default=None, max_length=30)

    @field_validator("contacts")
    @classmethod
    def contacts_limit(cls, value):
        if len(value) > 12:
            raise ValueError("Слишком много контактов")
        return value


class DeleteBody(Body):
    confirm: Literal["УДАЛИТЬ"]


@api_view
async def telegram_link(request: HttpRequest):
    alumni = await verified_alumni(request)
    state = request.services
    if not state.settings.secret("TELEGRAM_BOT_TOKEN"):
        raise ApiError(503, "Подключение Telegram пока недоступно.")
    code = await make_link(state.database, alumni["id"])
    return {
        "linked": bool(alumni["telegram_id"]),
        "url": f"https://t.me/{state.settings.TELEGRAM_BOT_USERNAME}?start={code}",
    }


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
    social = await social_progress(state, alumni["id"], alumni["telegram_id"]) if verified else None
    stats = await state.gamification.stats(alumni, ledger) if verified else None
    if verified:
        await state.gamification.refresh_achievements(alumni["id"])
    profile = {
        key: alumni[key]
        for key in ("fio", "cohort", "verification_status", "edu_program", "edu_level", "avatar", "referral_code")
    }
    profile.update(
        telegram_linked=bool(alumni["telegram_id"]),
        telegram_available=bool(state.settings.secret("TELEGRAM_BOT_TOKEN")),
        contacts=alumni["contacts_json"] or {},
        interests=alumni["interests_json"] or [],
        referrals_verified=sum(row["verification_status"] == "verified" for row in referred),
        referrals_pending=sum(row["verification_status"] == "pending" for row in referred),
    )
    result = {
        "alumni": profile,
        "level": level,
        "achievements": achievement_progress({**stats, **social}) if social else [],
        "activity": last_six_months(ledger) if verified else [],
    }
    if social:
        result["social"] = {key: social[key] for key in ("subscription", "reactions_available")}
    return result


@api_view(body=ProfileBody)
async def profile(request: HttpRequest):
    alumni = await verified_alumni(request)
    body = parse_body(request, ProfileBody)
    data = body.model_dump(exclude_unset=True)
    patch = {}
    if "fio" in data:
        patch["fio"] = data["fio"]
    if "contacts" in data:
        patch["contacts_json"] = data["contacts"]
    if "interests" in data:
        patch["interests_json"] = list(
            dict.fromkeys(value for value in data["interests"] if value in request.services.domain["legal_interests"])
        )[:MAX_INTERESTS]
    if patch:
        await request.services.store.update("alumni", patch, id=alumni["id"])
    return {"ok": True}


@api_view(permission=require_alumni)
async def export(request: HttpRequest):
    response = JsonResponse({}, safe=False)
    alumni = await require_alumni(request)
    store, id = (request.services.store, alumni["id"])
    profile, orders, ledger, friends, subscriptions = await asyncio.gather(
        store.read(
            "alumni",
            filters={"id": {"_eq": id}},
            fields=(
                "fio",
                "cohort",
                "edu_level",
                "edu_program",
                "verification_status",
                "points_cached",
                "level_cached",
                "personal_discount",
                "interests_json",
                "contacts_json",
                "telegram_id",
                "referral_code",
                "consent_at",
                "consent_version",
                "joined_at",
            ),
            limit=1,
        ),
        store.read(
            "orders",
            filters={"alumni_id": {"_eq": id}},
            fields=(
                "number",
                "type",
                "status",
                "payment_status",
                "subtotal",
                "total_estimate",
                "items_json",
                "contact_fio",
                "contact_phone",
                "contact_email",
                "created_at",
            ),
            sort=("-created_at",),
            limit=-1,
        ),
        store.read(
            "points_ledger",
            filters={"alumni_id": {"_eq": id}},
            fields=("delta", "reason", "comment", "created_at"),
            sort=("-created_at",),
            limit=-1,
        ),
        store.read(
            "alumni_friends",
            filters={"_or": [{"alumni_id": {"_eq": id}}, {"friend_id": {"_eq": id}}]},
            fields=("alumni_id", "friend_id", "status", "created_at"),
            limit=-1,
        ),
        store.read("push_subs", filters={"alumni_id": {"_eq": id}}, fields=("endpoint", "created_at"), limit=-1),
    )
    await audit(request, "alumni.self_export", actor="alumni:" + id, subject="alumni:" + id)
    response.headers["content-disposition"] = 'attachment; filename="moi-dannye-kluba.json"'
    response.content = JsonResponse(
        {
            "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "profile": profile[0] if profile else None,
            "orders": orders,
            "points_ledger": ledger,
            "friends": friends,
            "push_subscriptions": subscriptions,
        },
        safe=False,
    ).content
    return response


@api_view(body=DeleteBody, permission=require_alumni)
async def delete_me(request: HttpRequest):
    parse_body(request, DeleteBody)
    alumni = await require_alumni(request)
    await anonymize(request.services, alumni["id"])
    await audit(request, "alumni.self_delete", actor="alumni:" + alumni["id"], subject="alumni:" + alumni["id"])
    return {"ok": True}
