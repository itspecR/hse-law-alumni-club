import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from club_api.modules.auth.codes import CONFIRM_PURPOSE, RESET_PURPOSE
from club_api.modules.auth.passwords import hash_password


async def fixture(app, role="alumni", *, status="active", provider="default", tfa=None, ids=None):
    database = app.state.database
    role_id, user_id, alumni_id = ids or (str(uuid4()) for _ in range(3))
    hashed = await hash_password("synthetic-old-password")
    await database.execute("INSERT INTO directus_roles(id,name) VALUES(%s,%s)", (role_id, role))
    await database.execute(
        "INSERT INTO directus_users(id,email,password,role,status,provider,tfa_secret) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (user_id, user_id + "@example.test", hashed, role_id, status, provider, tfa),
    )
    await database.execute(
        "INSERT INTO alumni(id,user_id,fio,token_version) VALUES(%s,%s,'Тестовый профиль',0)", (alumni_id, user_id)
    )
    return user_id, alumni_id


@pytest.mark.asyncio
async def test_roles_existing_passwords_and_one_time_reset(database_app):
    app, auth, db = database_app, database_app.state.auth, database_app.state.database
    user_id, alumni_id = await fixture(app)
    original = auth.session(alumni_id, user_id)
    status, user = await auth.authenticate(user_id + "@example.test", "synthetic-old-password", scope="alumni")
    assert status == "ok" and user["alumni_version"] == 0
    assert "password" not in user
    code = await auth.send_code(user_id, RESET_PURPOSE)
    results = await asyncio.gather(*(auth.reset(user_id, "synthetic-new-password", code) for _ in range(2)))
    assert sorted(results) == ["invalid", "reset"]
    profile = await auth.profile(user_id)
    assert profile["token_version"] == 1
    assert (await auth.authenticate(user_id + "@example.test", "synthetic-old-password", scope="alumni"))[
        0
    ] == "invalid"
    assert (await auth.authenticate(user_id + "@example.test", "synthetic-new-password", scope="alumni"))[0] == "ok"
    from request_helpers import request_from_scope as Request

    scope = {"type": "http", "app": app, "headers": [(b"authorization", ("Bearer " + original).encode())]}
    assert await auth.resolve_alumni(Request(scope)) is None
    stored = (await db.rows("SELECT code_hash FROM club_email_codes WHERE user_id=%s", (user_id,)))[0]
    assert stored["code_hash"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "provider", "tfa", "scope", "expected"),
    [
        ("admin", "default", None, "alumni", "forbidden"),
        ("editor", "default", None, "admin", "ok"),
        ("alumni", "default", None, "admin", "forbidden"),
        ("alumni", "external-sso", None, "alumni", "invalid"),
        ("alumni", "default", "synthetic-mfa-factor", "alumni", "invalid"),
    ],
)
async def test_no_role_or_sso_bypass(database_app, role, provider, tfa, scope, expected):
    user_id, _ = await fixture(database_app, role, provider=provider, tfa=tfa)
    assert (
        await database_app.state.auth.authenticate(user_id + "@example.test", "synthetic-old-password", scope=scope)
    )[0] == expected
    if role != "alumni" or provider != "default" or tfa:
        assert await database_app.state.auth.user(id=user_id, alumni=True) is None
        assert await database_app.state.auth.reset(user_id, "synthetic-new-password", "000000") == "invalid"


@pytest.mark.asyncio
async def test_current_office_role_and_revocation_survive_new_service(database_app):
    from request_helpers import request_from_scope as Request

    from club_api.modules.auth.service import AuthService

    app = database_app
    user_id, _ = await fixture(app, "admin")
    token = app.state.auth.staff_session(user_id, "admin")
    request = Request({"type": "http", "app": app, "headers": [(b"authorization", ("Bearer " + token).encode())]})
    assert (await app.state.auth.resolve_admin(request))["role"] == "admin"
    editor_id = str(uuid4())
    await app.state.database.execute("INSERT INTO directus_roles(id,name) VALUES(%s,'editor')", (editor_id,))
    await app.state.database.execute("UPDATE directus_users SET role=%s WHERE id=%s", (editor_id, user_id))
    assert (await app.state.auth.resolve_admin(request))["role"] == "editor"
    jti = app.state.auth.decode(token, admin=True)["jti"]
    await app.state.auth.revoke_admin(jti)
    restarted = AuthService(app.state.settings, app.state.database, app.state.store)
    assert await restarted.resolve_admin(request) is None


@pytest.mark.asyncio
async def test_legacy_jwt_compatibility(database_app):
    app = database_app
    legacy = json.loads(Path(__file__).with_name("legacy-auth.json").read_text())
    await fixture(app, ids=(str(uuid4()), legacy["user"], legacy["alumni"]))
    payload = app.state.auth.decode(legacy["token"])
    assert payload["sub"] == legacy["user"] and payload["alumni_id"] == legacy["alumni"]
    from request_helpers import request_from_scope as Request

    request = Request(
        {"type": "http", "app": app, "headers": [(b"authorization", ("Bearer " + legacy["token"]).encode())]}
    )
    assert (await app.state.auth.resolve_alumni(request))["id"] == legacy["alumni"]


@pytest.mark.asyncio
async def test_registration_confirmation_and_secret_projection(database_app):
    app = database_app
    _, previous_alumni_id = await fixture(app)
    await app.state.database.execute(
        "UPDATE alumni SET consent_version='2026-07-02' WHERE id=%s", (previous_alumni_id,)
    )
    body = {
        "fio": "Тестовая анкета",
        "email": "synthetic@example.test",
        "password": "synthetic-password",
        "cohort": "2020",
        "edu_level": "магистратура",
        "edu_program": "Право",
        "consent_pdn": True,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/auth/register", json=body)).status_code == 200
        assert (await client.post("/auth/register", json=body)).status_code == 409
        login = await client.post("/auth/login", json={"email": body["email"].upper(), "password": body["password"]})
        assert login.status_code == 200
        user = await app.state.auth.user(email=body["email"])
        consents = await app.state.database.rows(
            "SELECT consent_version,consent_at FROM alumni WHERE user_id=%s", (user["id"],)
        )
        assert consents[0]["consent_version"] == "2026-11-06"
        assert consents[0]["consent_at"]
        previous = await app.state.database.rows(
            "SELECT consent_version FROM alumni WHERE id=%s", (previous_alumni_id,)
        )
        assert previous[0]["consent_version"] == "2026-07-02"
        users = await app.state.store.read("directus_users", filters={"id": {"_eq": user["id"]}})
        assert set(users[0]) == {"id", "email", "first_name", "last_name", "status", "role"}
        assert (await client.get("/ready")).status_code == 200
        await app.state.database.execute("UPDATE directus_users SET status='unverified' WHERE id=%s", (user["id"],))
        code = await app.state.auth.send_code(user["id"], CONFIRM_PURPOSE)
        confirm = {"email": body["email"], "code": code}
        assert (await client.post("/auth/confirm", json=confirm)).json() == {"ok": True}
        assert (await client.post("/auth/confirm", json=confirm)).json() == {"ok": True, "already": True}
