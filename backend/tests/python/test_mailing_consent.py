import httpx
from test_auth_integration import fixture
from test_community_integration import account

from club_api.modules.members.mailing import MAILING_CONSENT_VERSION
from club_api.modules.members.service import anonymize
from club_api.resources import shared_data

REGISTRATION = {
    "fio": "Вымышленный Выпускник",
    "password": "synthetic-password",
    "cohort": "2020",
    "edu_level": "магистратура",
    "edu_program": "Право",
    "consent_pdn": True,
}


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def consent(app, alumni_id):
    rows = await app.state.database.rows(
        "SELECT mailing_consent_at,mailing_consent_version FROM alumni WHERE id=%s", (alumni_id,)
    )
    return rows[0]["mailing_consent_at"], rows[0]["mailing_consent_version"]


async def registered(app, client, email, extra):
    assert (await client.post("/auth/register", json={**REGISTRATION, "email": email, **extra})).status_code == 200
    login = (await client.post("/auth/login", json={"email": email, "password": REGISTRATION["password"]})).json()
    me = (await client.get("/me", headers={"authorization": "Bearer " + login["token"]})).json()
    user = await app.state.auth.user(email=email)
    profile = await app.state.auth.profile(user["id"])
    return me, profile["id"]


def test_consent_version_comes_from_legal_data():
    assert MAILING_CONSENT_VERSION == shared_data("legal-data.json")["mailing_consent"]["version"]


async def test_registration_records_mailing_consent_only_when_given(database_app):
    app = database_app
    await fixture(app)
    async with client_for(app) as client:
        given, given_id = await registered(app, client, "given@example.test", {"consent_mailing": True})
        silent, silent_id = await registered(app, client, "silent@example.test", {})
    at, version = await consent(app, given_id)
    assert given["alumni"]["mailing_consent"] is True and at and version == MAILING_CONSENT_VERSION
    assert silent["alumni"]["mailing_consent"] is False and await consent(app, silent_id) == (None, None)


async def test_profile_withdraws_and_gives_consent_with_audit(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    async with client_for(app) as client:
        assert (await client.get("/me", headers=headers)).json()["alumni"]["mailing_consent"] is False
        given = await client.patch("/me/profile", headers=headers, json={"mailing_consent": True})
        at, version = await consent(app, alumni_id)
        again = await client.patch("/me/profile", headers=headers, json={"mailing_consent": True})
        same_at, _ = await consent(app, alumni_id)
        withdrawn = await client.patch("/me/profile", headers=headers, json={"mailing_consent": False})
        untouched = await client.patch("/me/profile", headers=headers, json={"fio": "Вымышленный участник"})
    assert given.status_code == again.status_code == withdrawn.status_code == untouched.status_code == 200
    assert at and version == MAILING_CONSENT_VERSION and same_at == at
    assert await consent(app, alumni_id) == (None, None)
    events = await app.state.database.rows(
        "SELECT event FROM audit_log WHERE subject=%s AND event LIKE %s",
        ("alumni:" + alumni_id, "mailing.%"),
    )
    assert sorted(row["event"] for row in events) == ["mailing.consent", "mailing.withdraw"]


async def test_deleted_profile_loses_mailing_consent(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    async with client_for(app) as client:
        await client.patch("/me/profile", headers=headers, json={"mailing_consent": True})
    await anonymize(app.state, alumni_id)
    assert await consent(app, alumni_id) == (None, None)
