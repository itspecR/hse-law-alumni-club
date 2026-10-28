import re
import time
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.models import count, group_count, guid, parse_date, partial, query_page
from club_api.core.views import api_view, defer, parse_body
from club_api.db.queries import acquire_lock
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin
from club_api.modules.catalog.scope import PUBLISHED_DPO
from club_api.modules.events.notifications import announce
from club_api.modules.gamification.routes import verified_alumni
from club_api.observability.audit import audit

ROUTE_LIMITS = {("POST", "/events/{id}/rsvp"): 20}
CLUB_TIMEZONE = ZoneInfo("Europe/Moscow")
UNNAMED = "ФИО не указано"
EVENT_FIELDS = ("id", "title", "description", "starts_at", "location", "cover", "reg_url", "format", "points", "status")


class EventBody(Body):
    title: str = Field(min_length=3)
    description: str | None = None
    starts_at: str = Field(min_length=4)
    location: str | None = None
    cover: str | None = Field(default=None, max_length=500)
    reg_url: str | None = Field(default=None, max_length=500)
    format: Literal["offline", "online"] = "offline"
    points: int = Field(default=60, ge=0, le=500)
    status: Literal["draft", "published", "done", "canceled"] = "published"

    @field_validator("reg_url")
    @classmethod
    def valid_url(cls, value):
        if not value:
            return None
        url = urlsplit(value)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password:
            raise ValueError("Некорректная ссылка")
        return value

    @field_validator("starts_at")
    @classmethod
    def valid_date(cls, value):
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value if moment.tzinfo else moment.replace(tzinfo=CLUB_TIMEZONE).isoformat()


EventPatch = partial(EventBody)


async def roster(store, event_id=None):
    rows = await store.read(
        "event_rsvps",
        filters={"event_id": {"_eq": event_id}} if event_id else None,
        fields=("id", "event_id", "alumni_id", "attended"),
        limit=-1,
    )
    names = (
        {
            row["id"]: row["fio"] or UNNAMED
            for row in await store.read(
                "alumni",
                filters={"id": {"_in": list({row["alumni_id"] for row in rows})}},
                fields=("id", "fio"),
                limit=-1,
            )
        }
        if rows
        else {}
    )
    return [{**row, "fio": names.get(row["alumni_id"], UNNAMED)} for row in rows]


@api_view
async def stats(request: HttpRequest):
    state = request.services
    cached = getattr(state, "public_stats", None)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    data = {
        "alumni": await count(state.store, "alumni", {"verification_status": {"_eq": "verified"}}),
        "events": await count(state.store, "events", {"status": {"_in": ["published", "done"]}}),
        "programs": await count(state.store, "programs", PUBLISHED_DPO),
    }
    state.public_stats = (time.monotonic(), data)
    return data


@api_view
async def events(request: HttpRequest):
    state = request.services
    alumni = await state.auth.resolve_alumni(request)
    filters = {"status": {"_in": ["published", "done"]}}
    month = request.GET.get("month")
    id = request.GET.get("id")
    if month is not None:
        try:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
                raise ValueError
            start = datetime.strptime(month, "%Y-%m").replace(tzinfo=CLUB_TIMEZONE)
            end = (
                start.replace(year=start.year + 1, month=1)
                if start.month == 12
                else start.replace(month=start.month + 1)
            )
        except ValueError:
            raise ApiError(400, "Некорректный месяц.") from None
        filters["starts_at"] = {"_gte": start, "_lt": end}
    if id is not None:
        filters["id"] = {"_eq": guid(id)}
    if month is not None or id is not None:
        rows = await state.store.read(
            "events", filters=filters, sort=("starts_at", "id"), fields=EVENT_FIELDS, limit=50
        )
    else:
        now = datetime.now(UTC)
        upcoming = await state.store.read(
            "events",
            filters={**filters, "starts_at": {"_gte": now}},
            sort=("starts_at", "id"),
            fields=EVENT_FIELDS,
            limit=50,
        )
        past = await state.store.read(
            "events",
            filters={**filters, "starts_at": {"_lt": now}},
            sort=("-starts_at", "id"),
            fields=EVENT_FIELDS,
            limit=50,
        )
        rows = sorted([*past, *upcoming], key=lambda row: (row["starts_at"], row["id"]))
    ids = [row["id"] for row in rows]
    groups = await group_count(state.store, "event_rsvps", ("event_id",), {"event_id": {"_in": ids}}) if ids else []
    counts = {row["event_id"]: row["count"] for row in groups}
    mine = (
        await state.store.read(
            "event_rsvps",
            filters={"event_id": {"_in": ids}, "alumni_id": {"_eq": alumni["id"]}},
            fields=("event_id", "attended"),
            limit=-1,
        )
        if ids and alumni
        else []
    )
    attended = {row["event_id"]: row["attended"] for row in mine}
    return [
        {
            **row,
            "going": counts.get(row["id"], 0),
            "my_rsvp": row["id"] in attended,
            "my_attended": attended.get(row["id"], False),
        }
        for row in rows
    ]


def ics_escape(value):
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
    )


@api_view
async def calendar(request: HttpRequest, id: str):
    rows = await request.services.store.read(
        "events",
        filters={"id": {"_eq": guid(id)}, "status": {"_in": ["published", "done"]}},
        fields=EVENT_FIELDS,
        limit=1,
    )
    if not rows:
        raise ApiError(404, "Событие не найдено.")
    event = rows[0]
    start = parse_date(event["starts_at"])

    def stamp(value):
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Клуб выпускников факультета права Вышки//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:event-{id}@club-pravo-hse",
        "DTSTAMP:" + stamp(datetime.now(UTC)),
        "DTSTART:" + stamp(start),
        "DTEND:" + stamp(start + timedelta(hours=2)),
        "SUMMARY:" + ics_escape(event["title"]),
    ]
    if event["description"]:
        description = event["description"] + ("\nРегистрация: " + event["reg_url"] if event["reg_url"] else "")
        lines.append("DESCRIPTION:" + ics_escape(description))
    if event["location"] and event["format"] != "online":
        lines.append("LOCATION:" + ics_escape(event["location"]))
    lines.extend(
        [
            "URL:" + request.services.settings.PUBLIC_URL + "/events",
            "BEGIN:VALARM",
            "TRIGGER:-PT2H",
            "ACTION:DISPLAY",
            "DESCRIPTION:" + ics_escape("Через 2 часа: " + event["title"]),
            "END:VALARM",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )
    return HttpResponse(
        "\r\n".join(lines),
        content_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="club-event.ics"'},
    )


@api_view
async def rsvp(request: HttpRequest, id: str):
    alumni = await verified_alumni(request)
    state, id = (request.services, guid(id))
    async with state.database.transaction() as connection:
        await acquire_lock(connection, "rsvp:" + id + ":" + alumni["id"])
        rows = await state.store.read(
            "events",
            filters={"id": {"_eq": id}, "status": {"_eq": "published"}},
            fields=("id",),
            limit=1,
            connection=connection,
        )
        if not rows:
            raise ApiError(404, "Событие не найдено.")
        existing = await state.store.read(
            "event_rsvps",
            filters={"event_id": {"_eq": id}, "alumni_id": {"_eq": alumni["id"]}},
            fields=("id", "attended"),
            limit=1,
            connection=connection,
        )
        if existing:
            if existing[0]["attended"]:
                raise ApiError(400, "Посещение уже отмечено, запись отменить нельзя.")
            await state.store.delete("event_rsvps", id=existing[0]["id"], connection=connection)
            return {"going": False}
        await state.store.create(
            "event_rsvps", {"event_id": id, "alumni_id": alumni["id"], "attended": False}, connection=connection
        )
    await audit(request, "event.rsvp", actor="alumni:" + alumni["id"], subject="event:" + id)
    return {"going": True}


@api_view(permission=require_admin)
async def admin_events(request: HttpRequest):
    await require_admin(request)
    store = request.services.store
    if "page" in request.GET or "limit" in request.GET:
        page, limit = query_page(request, default_limit=20)
        rows = await store.read(
            "events", sort=("-starts_at", "id"), fields=EVENT_FIELDS, limit=limit, offset=(page - 1) * limit
        )
        counts = (
            {
                row["event_id"]: row["count"]
                for row in await group_count(
                    store, "event_rsvps", ("event_id",), {"event_id": {"_in": [row["id"] for row in rows]}}
                )
            }
            if rows
            else {}
        )
        return {
            "items": [{**row, "rsvp_count": counts.get(row["id"], 0)} for row in rows],
            "total": await count(store, "events"),
            "page": page,
            "limit": limit,
        }
    rows = await store.read("events", sort=("-starts_at",), fields=EVENT_FIELDS, limit=-1)
    rsvps = await roster(store)
    return [
        {
            **row,
            "rsvps": [
                {key: value for key, value in rsvp.items() if key != "event_id"}
                for rsvp in rsvps
                if rsvp["event_id"] == row["id"]
            ],
        }
        for row in rows
    ]


@api_view(permission=require_admin)
async def admin_roster(request: HttpRequest, id: str):
    await require_admin(request)
    await request.services.store.one("events", guid(id), fields=("id",))
    return [
        {key: value for key, value in row.items() if key != "event_id"}
        for row in await roster(request.services.store, id)
    ]


@api_view(body=EventBody, permission=require_admin)
async def create_event(request: HttpRequest):
    admin = await require_admin(request)
    body = parse_body(request, EventBody)
    row = await request.services.store.create("events", body.model_dump())
    await audit(
        request,
        "event.create",
        actor="admin:" + admin["userId"],
        subject="event:" + row["id"],
        detail={"title": body.title},
    )
    if body.status == "published":
        defer(request, announce, request.services, row)
    return {"ok": True, "id": row["id"]}


@api_view(body=EventPatch, permission=require_admin)
async def patch_event(request: HttpRequest, id: str):
    admin = await require_admin(request)
    body = parse_body(request, EventPatch)
    data = body.model_dump(exclude_unset=True)
    await request.services.store.update("events", data, id=guid(id))
    await audit(request, "event.patch", actor="admin:" + admin["userId"], subject="event:" + id, detail=data)
    return {"ok": True}


@api_view(permission=require_admin)
async def delete_event(request: HttpRequest, id: str):
    admin = await require_admin(request)
    await request.services.store.delete("events", id=guid(id))
    await audit(request, "event.delete", actor="admin:" + admin["userId"], subject="event:" + id)
    return {"ok": True}


@api_view(permission=require_admin)
async def attend(request: HttpRequest, rsvpId: str):
    admin = await require_admin(request)
    state = request.services
    row = await state.store.one("event_rsvps", guid(rsvpId))
    if not row:
        raise ApiError(404, "Запись на событие не найдена.")
    async with state.database.transaction() as connection:
        await acquire_lock(connection, "rsvp:" + row["event_id"] + ":" + row["alumni_id"])
        cursor = await connection.execute("SELECT attended FROM event_rsvps WHERE id=%s FOR UPDATE", (row["id"],))
        current = await cursor.fetchone()
        if not current:
            raise ApiError(404, "Запись на событие не найдена.")
        if current["attended"]:
            return {"ok": True, "already": True}
        event = await state.store.one("events", row["event_id"], connection=connection)
        await state.gamification.add_in_transaction(
            connection,
            row["alumni_id"],
            reason="event",
            delta=event.get("points", 60) if event else 60,
            ref=row["event_id"],
            comment="Участие: " + (event["title"] if event else "событие клуба"),
            idempotency_key=f"event-{row['event_id']}-{row['alumni_id']}",
        )
        await state.store.update("event_rsvps", {"attended": True}, id=row["id"], connection=connection)
    await state.gamification.refresh_achievements(row["alumni_id"])
    await audit(
        request,
        "event.attended",
        actor="admin:" + admin["userId"],
        subject="alumni:" + row["alumni_id"],
        detail={"event": row["event_id"]},
    )
    return {"ok": True}
