import math
from datetime import UTC, datetime, timedelta
from uuid import UUID

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body, parse_uuid
from club_api.db.queries import acquire_lock
from club_api.db.store import normalize
from club_api.domain import compute_level
from club_api.modules.auth.routes import Body
from club_api.modules.gamification.routes import verified_alumni
from club_api.observability.audit import audit

ROUTE_LIMITS = {"/me/classmates": 30, "/me/friends": 20, "/me/friends/{alumniId}": 20}
ALUMNI_NOT_FOUND = "Выпускник не найден."


class FriendBody(Body):
    alumni_id: str


def pair_filter(first, second):
    return {
        "_or": [
            {"alumni_id": {"_eq": first}, "friend_id": {"_eq": second}},
            {"alumni_id": {"_eq": second}, "friend_id": {"_eq": first}},
        ]
    }


@api_view
async def classmates(request: HttpRequest):
    me = await verified_alumni(request)
    store = request.services.store
    alternatives = [{name: {"_eq": me[name]}} for name in ("cohort", "edu_program") if me[name]]
    if not alternatives:
        return []
    rows = await store.read(
        "alumni",
        filters={
            "_and": [{"verification_status": {"_eq": "verified"}}, {"id": {"_neq": me["id"]}}, {"_or": alternatives}]
        },
        limit=100,
        fields=("id", "fio", "cohort", "edu_program", "edu_level", "points_cached", "interests_json", "avatar"),
    )
    links = await store.read(
        "alumni_friends",
        filters={"_or": [{"alumni_id": {"_eq": me["id"]}}, {"friend_id": {"_eq": me["id"]}}]},
        fields=("alumni_id", "friend_id", "status"),
        limit=-1,
    )
    result = []
    for row in rows:
        pair = [link for link in links if {link["alumni_id"], link["friend_id"]} == {me["id"], row["id"]}]
        status = (
            "accepted"
            if any(link["status"] == "accepted" for link in pair)
            else "incoming"
            if any(link["alumni_id"] == row["id"] for link in pair)
            else "pending"
            if pair
            else "none"
        )
        same_cohort, same_program = (
            bool(me["cohort"] and row["cohort"] == me["cohort"]),
            bool(me["edu_program"] and row["edu_program"] == me["edu_program"]),
        )
        result.append(
            {
                **{key: row[key] for key in ("id", "fio", "cohort", "edu_program", "edu_level", "avatar")},
                "level_title": compute_level(row["points_cached"] or 0)["title"],
                "interests": row["interests_json"] or [],
                "match": "both" if same_cohort and same_program else "cohort" if same_cohort else "program",
                "friend_status": status,
            }
        )
    return result


@api_view
async def notifications(request: HttpRequest):
    me = await verified_alumni(request)
    store = request.services.store
    now, events = (datetime.now(UTC), [])
    cutoff = normalize(now - timedelta(days=30))
    incoming = await store.read(
        "alumni_friends",
        filters={"friend_id": {"_eq": me["id"]}, "status": {"_eq": "pending"}},
        fields=("alumni_id", "created_at"),
        sort=("-created_at",),
        limit=20,
    )
    accepted = await store.read(
        "alumni_friends",
        filters={"alumni_id": {"_eq": me["id"]}, "status": {"_eq": "accepted"}, "created_at": {"_gte": cutoff}},
        fields=("friend_id", "created_at"),
        sort=("-created_at",),
        limit=20,
    )
    ids = list({row["alumni_id"] for row in incoming} | {row["friend_id"] for row in accepted})
    names = (
        {
            row["id"]: row["fio"]
            for row in await store.read("alumni", filters={"id": {"_in": ids}}, fields=("id", "fio"), limit=-1)
        }
        if ids
        else {}
    )
    events.extend(
        {
            "kind": "friend_request",
            "from_id": row["alumni_id"],
            "from_fio": names.get(row["alumni_id"]),
            "created_at": row["created_at"],
        }
        for row in incoming
    )
    events.extend(
        {"kind": "friend_accepted", "by_fio": names.get(row["friend_id"]), "created_at": row["created_at"]}
        for row in accepted
    )
    orders = await store.read(
        "orders",
        filters={"alumni_id": {"_eq": me["id"]}, "status": {"_neq": "new"}, "created_at": {"_gte": cutoff}},
        fields=("number", "status", "payment_status", "created_at"),
        sort=("-created_at",),
        limit=10,
    )
    events.extend(
        {
            "kind": "order_status",
            "number": row["number"],
            "status": row["status"],
            "paid": row["payment_status"] == "succeeded",
            "created_at": row["created_at"],
        }
        for row in orders
    )
    if me["podcast_sub_until"]:
        days = math.ceil(
            (datetime.fromisoformat(me["podcast_sub_until"].replace("Z", "+00:00")) - now).total_seconds() / 86400
        )
        if 0 < days <= 14:
            events.append({"kind": "podcast_expiring", "days_left": days, "until": me["podcast_sub_until"]})
    return events


@api_view(body=FriendBody)
async def friend(request: HttpRequest):
    me = await verified_alumni(request)
    body = parse_body(request, FriendBody)
    try:
        UUID(body.alumni_id)
    except ValueError as error:
        raise ApiError(400, ALUMNI_NOT_FOUND) from error
    if body.alumni_id == me["id"]:
        raise ApiError(400, "Нельзя добавить в друзья себя.")
    state = request.services
    target = await state.store.one("alumni", body.alumni_id, fields=("verification_status",))
    if target["verification_status"] != "verified":
        raise ApiError(404, ALUMNI_NOT_FOUND)
    async with state.database.transaction() as connection:
        key = "friends:" + ":".join(sorted((me["id"], body.alumni_id)))
        await acquire_lock(connection, key)
        links = await state.store.read(
            "alumni_friends",
            filters=pair_filter(me["id"], body.alumni_id),
            fields=("id", "alumni_id", "status"),
            limit=-1,
            connection=connection,
        )
        if any(link["status"] == "accepted" for link in links):
            return {"status": "accepted"}
        incoming = next(
            (link for link in links if link["status"] == "pending" and link["alumni_id"] == body.alumni_id), None
        )
        if incoming:
            await state.store.update("alumni_friends", {"status": "accepted"}, id=incoming["id"], connection=connection)
            status = "accepted"
        elif links:
            return {"status": "pending"}
        else:
            await state.store.create(
                "alumni_friends",
                {"alumni_id": me["id"], "friend_id": body.alumni_id, "status": "pending"},
                connection=connection,
            )
            status = "pending"
    await state.push.to_alumni(
        body.alumni_id,
        {
            "title": "Приглашение в друзья принято" if status == "accepted" else "Приглашение в друзья",
            "body": (me["fio"] or "Выпускник")
            + (": приглашение в друзья принято." if status == "accepted" else " приглашает вас в друзья."),
            "url": "/lk",
        },
    )
    return {"status": status}


@api_view
async def remove_friend(request: HttpRequest, alumniId: UUID):
    alumniId = parse_uuid(alumniId)
    me = await verified_alumni(request)
    other = str(alumniId)
    store = request.services.store
    links = await store.read("alumni_friends", filters=pair_filter(me["id"], other), fields=("status",), limit=-1)
    if links:
        await store.delete("alumni_friends", filters=pair_filter(me["id"], other))
        await audit(
            request,
            "friend.remove" if any(link["status"] == "accepted" for link in links) else "friend.decline",
            actor="alumni:" + me["id"],
            subject="alumni:" + other,
        )
    return {"status": "none"}
