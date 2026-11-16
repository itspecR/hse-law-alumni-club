from test_mailing_consent import REGISTRATION, client_for

from club_api.core.config import Settings
from club_api.modules.auth.service import REGISTRATION_CLOSED, registration_open

EMAIL = "closed@example.test"


def test_registration_is_closed_unless_the_server_opens_it():
    assert not registration_open(Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only"))
    assert registration_open(
        Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only", REGISTRATION_ENABLED="true")
    )


async def test_closed_registration_refuses_new_accounts(database_app, monkeypatch):
    app = database_app
    monkeypatch.setattr(app.state.settings, "REGISTRATION_ENABLED", "false")
    async with client_for(app) as client:
        state = (await client.get("/auth/registration")).json()
        reply = await client.post("/auth/register", json={**REGISTRATION, "email": EMAIL})
        sitemap = (await client.get("/sitemap.xml")).text
    assert state == {"enabled": False}
    assert (reply.status_code, reply.json()["error"]) == (403, REGISTRATION_CLOSED)
    assert await app.state.auth.user(email=EMAIL) is None
    assert "/join</loc>" not in sitemap and "/about</loc>" in sitemap


async def test_open_registration_is_reported_to_the_site(database_app):
    async with client_for(database_app) as client:
        state = (await client.get("/auth/registration")).json()
        sitemap = (await client.get("/sitemap.xml")).text
    assert state == {"enabled": True}
    assert "/join</loc>" in sitemap
