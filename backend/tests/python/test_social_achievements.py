import httpx
from test_community_integration import account

from club_api.domain import ACHIEVEMENTS
from club_api.modules.members import profile

TELEGRAM = ("on_radar", "on_wave")


async def subscribed(state, alumni_id, telegram_id=None):
    return {
        "telegram_subscribed": 1,
        "telegram_reactions": 10,
        "subscription": "subscribed",
        "reactions_available": True,
    }


async def test_telegram_achievements_are_stored(database_app, monkeypatch):
    app = database_app
    alumni_id, member = await account(app)
    created = {}
    for key in TELEGRAM:
        row = await app.state.store.create("achievements", next(item for item in ACHIEVEMENTS if item["key"] == key))
        created[row["id"]] = key
    monkeypatch.setattr(profile, "social_progress", subscribed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        me = (await client.get("/me", headers=member)).json()
    assert all(item["earned"] for item in me["achievements"] if item["key"] in TELEGRAM)
    stored = await app.state.store.read(
        "alumni_achievements", filters={"alumni_id": {"_eq": alumni_id}}, fields=("achievement_id",), limit=-1
    )
    assert {created[row["achievement_id"]] for row in stored} == set(TELEGRAM)
