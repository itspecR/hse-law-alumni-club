import httpx
from test_community_integration import account

from club_api.domain import ACHIEVEMENTS

TELEGRAM = {"on_radar", "on_wave"}


async def test_cabinet_has_no_telegram_link_export_or_telegram_achievements(database_app):
    app = database_app
    _, member = await account(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        me = (await client.get("/me", headers=member)).json()
        for path in ("/me/tg-link", "/me/export"):
            assert (await client.get(path, headers=member)).status_code == 404, path
    assert "social" not in me and not {"telegram_linked", "telegram_available"} & me["alumni"].keys()
    assert not TELEGRAM & {item["key"] for item in me["achievements"]}
    assert not TELEGRAM & {item["key"] for item in ACHIEVEMENTS}
