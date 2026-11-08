from collections import Counter
from datetime import UTC, datetime
from typing import Literal

from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.models import count, group_count, guid, query_choice, query_page, query_search, sub_active
from club_api.core.names import PersonName
from club_api.core.schemas import Body
from club_api.core.views import api_view, defer, parse_body
from club_api.domain import DOMAIN
from club_api.modules.auth.service import alumni_names, require_admin, require_full_admin
from club_api.modules.members.service import alumni_email, anonymize
from club_api.modules.notifications.mail import CLUB_NAME, club_subject
from club_api.observability.audit import audit

MEMBER_FIELDS = (
    "id",
    "user_id",
    "fio",
    "cohort",
    "status",
    "verification_status",
    "points_cached",
    "level_cached",
    "personal_discount",
    "podcast_sub_until",
    "edu_level",
    "edu_program",
    "interests_json",
    "contacts_json",
    "joined_at",
    "avatar",
)


class MemberPatch(Body):
    verification_status: Literal["pending", "verified", "rejected"] = None
    personal_discount: int = Field(default=None, ge=0, le=10)
    fio: PersonName = None


MANUAL_COMMENT = "Начисление клуба"
MENTORSHIP_NOTE_MIN = 3
POINTS_LIMIT = DOMAIN["limits"]["manual_points_max"]
COMMENT_LIMIT = DOMAIN["limits"]["points_comment_max"]
HISTORY_LIMIT = 100
HISTORY_FIELDS = ("id", "delta", "reason", "comment", "created_at")


class PointsBody(Body):
    reason: Literal["manual", "program", "mentorship"] = "manual"
    delta: int | None = Field(default=None, ge=-POINTS_LIMIT, le=POINTS_LIMIT)
    program_id: str | None = None
    comment: str | None = Field(default=None, max_length=COMMENT_LIMIT)


@api_view(permission=require_admin)
async def members(request: HttpRequest):
    await require_admin(request)
    store = request.services.store
    page, limit = query_page(request)
    status = query_choice(request, "status", ("pending", "verified", "rejected"))
    search, filters = (query_search(request), [])
    if status:
        filters.append({"verification_status": {"_eq": status}})
    if search:
        filters.append({"_or": [{field: {"_icontains": search}} for field in ("fio", "cohort", "edu_program")]})
    where = {"_and": filters} if filters else None
    rows = await store.read(
        "alumni",
        filters=where,
        fields=MEMBER_FIELDS,
        sort=("-points_cached", "id"),
        limit=limit,
        offset=(page - 1) * limit,
    )
    ids = [row["id"] for row in rows]
    fios = list({row["fio"] for row in rows if row["fio"]})
    links = (
        await store.read(
            "alumni_friends",
            filters={"status": {"_eq": "accepted"}, "_or": [{"alumni_id": {"_in": ids}}, {"friend_id": {"_in": ids}}]},
            fields=("alumni_id", "friend_id"),
            limit=-1,
        )
        if ids
        else []
    )
    friends = Counter(member for link in links for member in link.values())
    groups = (
        await group_count(store, "alumni", ("fio", "cohort"), {"fio": {"_in": fios}, "status": {"_neq": "alumni_left"}})
        if fios
        else []
    )

    def duplicate_key(row):
        return ((row["fio"] or "").strip().lower(), row["cohort"] or "")

    duplicates = Counter()
    for row in groups:
        duplicates[duplicate_key(row)] += row["count"]
    users = [row["user_id"] for row in rows if row["user_id"]]
    emails = (
        {
            row["id"]: row["email"]
            for row in await store.read(
                "directus_users", filters={"id": {"_in": users}}, fields=("id", "email"), limit=-1
            )
        }
        if users
        else {}
    )
    return {
        "items": [
            {
                **row,
                "email": emails.get(row["user_id"]) or (row["contacts_json"] or {}).get("email") or None,
                "friends_count": friends[row["id"]],
                "podcast_active": sub_active(row["podcast_sub_until"]),
                "duplicate": duplicates[duplicate_key(row)] > 1,
            }
            for row in rows
        ],
        "total": await count(store, "alumni", where),
        "page": page,
        "page_size": limit,
    }


@api_view(permission=require_full_admin)
async def grant_subscription(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    until = await request.services.payments.extend_subscription(guid(id))
    await audit(
        request, "podcast.sub.grant", actor="admin:" + admin["userId"], subject="alumni:" + id, detail={"until": until}
    )
    return {"ok": True, "until": until}


async def notify_verification(state, id, status):
    email = await alumni_email(state, id)
    if not email:
        return
    if status == "verified":
        subject = club_subject("Статус выпускника подтвержден")
        text = (
            "Здравствуйте!\n\nМы подтвердили Ваш статус выпускника, кабинет открыт.\n\nВойти: "
            + state.settings.PUBLIC_URL
            + "/lk\n\n"
            + CLUB_NAME
        )
    else:
        subject = club_subject("Заявка на вступление в клуб")
        text = (
            "Здравствуйте!\n\nМы не смогли подтвердить данные Вашей заявки. "
            "Если это ошибка, ответьте на это письмо или напишите в поддержку.\n\n" + CLUB_NAME
        )
    await state.notifications.send_email(email, subject, text)


def patch_detail(data):
    detail = {key: value for key, value in data.items() if key != "fio"}
    return {**detail, "fio_changed": True} if "fio" in data else detail


async def rename_user(state, user_id, fio):
    if user_id:
        await state.database.execute(
            "UPDATE directus_users SET first_name=%s,last_name=%s WHERE id=%s "
            "AND role IN (SELECT id FROM directus_roles WHERE name='alumni')",
            (*alumni_names(fio), user_id),
        )


@api_view(body=MemberPatch, permission=require_full_admin)
async def patch_member(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    body = parse_body(request, MemberPatch)
    state, id = (request.services, guid(id))
    data = body.model_dump(exclude_unset=True)
    patch = {**data}
    if data.get("verification_status") == "verified":
        patch["verified_at"] = datetime.now(UTC)
    row = await state.store.update("alumni", patch, id=id)
    if "fio" in data:
        await rename_user(state, row["user_id"], data["fio"])
    await audit(
        request, "member.patch", actor="admin:" + admin["userId"], subject="alumni:" + id, detail=patch_detail(data)
    )
    if data.get("verification_status") in ("verified", "rejected"):
        defer(request, notify_verification, state, id, data["verification_status"])
    if data.get("verification_status") == "verified" and row.get("referred_by"):
        await state.gamification.add(
            row["referred_by"],
            reason="referral",
            ref=id,
            comment="Приглашенный выпускник подтвержден",
            idempotency_key="referral-" + id,
        )
    return {"ok": True}


def note(body):
    return (body.comment or "").strip()


def manual_award(body):
    if not body.delta:
        raise ApiError(400, "Укажите изменение баллов, не равное нулю.")
    return {"reason": "manual", "delta": body.delta, "comment": note(body) or MANUAL_COMMENT}


def mentorship_award(body):
    if len(note(body)) < MENTORSHIP_NOTE_MIN:
        raise ApiError(400, "Напишите в комментарии, кого наставлял выпускник.")
    return {"reason": "mentorship", "comment": "Наставничество: " + note(body)}


async def program_award(state, alumni_id, body):
    program = await state.store.one("programs", guid(body.program_id)) if body.program_id else None
    if not program:
        raise ApiError(400, "Выберите программу из списка.")
    key = f"program-{alumni_id}-{program['id']}"
    if await state.store.read("points_ledger", filters={"idempotency_key": {"_eq": key}}, fields=("id",), limit=1):
        raise ApiError(409, f"За программу «{program['title']}» баллы уже начислены.")
    comment = "Программа: " + program["title"] + (". " + note(body) if note(body) else "")
    return {"reason": "program", "ref": program["id"], "comment": comment, "idempotency_key": key}


async def points_award(state, alumni_id, body):
    if body.reason == "program":
        return await program_award(state, alumni_id, body)
    return mentorship_award(body) if body.reason == "mentorship" else manual_award(body)


@api_view(permission=require_full_admin)
async def point_programs(request: HttpRequest):
    await require_full_admin(request)
    return await request.services.store.read(
        "programs", fields=("id", "title", "catalog"), sort=("catalog", "title"), limit=-1
    )


@api_view(permission=require_full_admin)
async def point_history(request: HttpRequest, id: str):
    await require_full_admin(request)
    store = request.services.store
    member = await store.one("alumni", guid(id))
    if not member:
        raise ApiError(404, "Выпускник не найден.")
    items = await store.read(
        "points_ledger",
        filters={"alumni_id": {"_eq": member["id"]}},
        fields=HISTORY_FIELDS,
        sort=("-created_at", "-id"),
        limit=HISTORY_LIMIT,
    )
    return {"id": member["id"], "fio": member["fio"], "points": member["points_cached"] or 0, "items": items}


@api_view(body=PointsBody, permission=require_full_admin)
async def points(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    body = parse_body(request, PointsBody)
    award = await points_award(request.services, guid(id), body)
    result = await request.services.gamification.add(guid(id), **award)
    await audit(
        request, "member.points", actor="admin:" + admin["userId"], subject="alumni:" + id, detail=body.model_dump()
    )
    return {"ok": True, **result}


@api_view(permission=require_full_admin)
async def erase(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    if not await anonymize(request.services, guid(id)):
        raise ApiError(404, "Выпускник не найден.")
    await audit(request, "admin.member.anonymize", actor="admin:" + admin["userId"], subject="alumni:" + id)
    return {"ok": True}
