from datetime import UTC, datetime

import httpx
from test_community_integration import account

from club_api.domain import ACHIEVEMENTS

POINTS = 75


async def attended_event(app, alumni_id):
    today = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = app.state.store
    event = await store.create(
        "events", {"title": "Встреча", "starts_at": today, "points": POINTS, "status": "published"}
    )
    return await store.create("event_rsvps", {"event_id": event["id"], "alumni_id": alumni_id, "attended": False})


def earned(me, key):
    return next(item for item in me["achievements"] if item["key"] == key)["earned"]


async def stored_achievements(app, alumni_id):
    return await app.state.store.read("alumni_achievements", filters={"alumni_id": {"_eq": alumni_id}}, limit=-1)


async def test_undo_attendance_returns_points_and_allows_marking_again(database_app):
    app = database_app
    alumni_id, member = await account(app)
    _, editor = await account(app, "editor")
    await app.state.store.create("achievements", next(item for item in ACHIEVEMENTS if item["key"] == "first_step"))
    rsvp = await attended_event(app, alumni_id)
    base = f"/admin/events/rsvp/{rsvp['id']}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.post(base + "/unattend", headers=editor)).json() == {"ok": True, "already": True}
        assert (await client.post(base + "/attend", headers=editor)).status_code == 200
        assert earned((await client.get("/me", headers=member)).json(), "first_step")
        assert await stored_achievements(app, alumni_id)
        assert (await client.post(base + "/unattend", headers=editor)).json() == {"ok": True}
        assert (await app.state.store.one("alumni", alumni_id))["points_cached"] == 0
        assert not (await app.state.store.one("event_rsvps", rsvp["id"]))["attended"]
        assert not earned((await client.get("/me", headers=member)).json(), "first_step")
        assert not await stored_achievements(app, alumni_id)
        assert (await client.post(base + "/attend", headers=editor)).status_code == 200
    ledger = await app.state.store.read("points_ledger", filters={"alumni_id": {"_eq": alumni_id}}, limit=-1)
    assert sorted(row["delta"] for row in ledger) == [-POINTS, POINTS, POINTS]
    assert {row["reason"] for row in ledger} == {"event", "event_undo"}
    assert (await app.state.store.one("alumni", alumni_id))["points_cached"] == POINTS
    assert (await app.state.store.one("event_rsvps", rsvp["id"]))["attended"]


async def test_member_cannot_undo_attendance(database_app):
    app = database_app
    alumni_id, member = await account(app)
    rsvp = await attended_event(app, alumni_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(f"/admin/events/rsvp/{rsvp['id']}/unattend", headers=member)
    assert response.status_code in (401, 403)
