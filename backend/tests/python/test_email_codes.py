from datetime import UTC, datetime, timedelta

import httpx
from test_auth_integration import fixture

from club_api.modules.auth.codes import (
    CODE_MAX_ATTEMPTS,
    CODE_RESEND,
    CODE_WINDOW_LIMIT,
    CONFIRM_PURPOSE,
    RESET_PURPOSE,
    clean_code,
    new_code,
)
from club_api.modules.auth.routes import CODE_INVALID
from club_api.modules.members.service import anonymize


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def unverified(app):
    user_id, alumni_id = await fixture(app, status="unverified")
    return user_id, alumni_id, user_id + "@example.test"


SHIFT_SQL = {
    "expires_at": "UPDATE club_email_codes SET expires_at=%s WHERE user_id=%s AND purpose=%s",
    "sent_at": "UPDATE club_email_codes SET sent_at=%s WHERE user_id=%s AND purpose=%s",
    "window_started_at": "UPDATE club_email_codes SET window_started_at=%s WHERE user_id=%s AND purpose=%s",
}


def other(code):
    return str((int(code) + 1) % 1000000).zfill(6)


async def shift(app, user_id, purpose, **columns):
    for column, delta in columns.items():
        await app.state.database.execute(SHIFT_SQL[column], (datetime.now(UTC) - delta, user_id, purpose))


async def status(app, user_id):
    return (await app.state.database.rows("SELECT status FROM directus_users WHERE id=%s", (user_id,)))[0]["status"]


def test_codes_have_six_digits_and_ignore_spaces():
    assert all(len(new_code()) == 6 and new_code().isdigit() for _ in range(50))
    assert clean_code(" 12 34-56 ") == "123456"


async def test_code_is_stored_as_hash_and_confirms_once(database_app):
    app = database_app
    user_id, _, email = await unverified(app)
    code = await app.state.auth.send_code(user_id, CONFIRM_PURPOSE)
    rows = await app.state.database.rows("SELECT code_hash FROM club_email_codes WHERE user_id=%s", (user_id,))
    assert len(rows[0]["code_hash"]) == 64 and code not in rows[0]["code_hash"]
    async with client_for(app) as client:
        wrong = await client.post("/auth/confirm", json={"email": email, "code": other(code)})
        accepted = await client.post("/auth/confirm", json={"email": email.upper(), "code": code[:3] + " " + code[3:]})
    assert wrong.status_code == 400 and wrong.json()["error"] == CODE_INVALID
    assert accepted.json() == {"ok": True} and await status(app, user_id) == "active"


async def test_code_burns_after_wrong_attempts(database_app):
    app = database_app
    user_id, _, email = await unverified(app)
    code = await app.state.auth.send_code(user_id, CONFIRM_PURPOSE)
    async with client_for(app) as client:
        for _ in range(CODE_MAX_ATTEMPTS):
            assert (await client.post("/auth/confirm", json={"email": email, "code": other(code)})).status_code == 400
        late = await client.post("/auth/confirm", json={"email": email, "code": code})
    assert late.status_code == 400 and await status(app, user_id) == "unverified"


async def test_expired_code_is_rejected(database_app):
    app = database_app
    user_id, _, email = await unverified(app)
    code = await app.state.auth.send_code(user_id, CONFIRM_PURPOSE)
    await shift(app, user_id, CONFIRM_PURPOSE, expires_at=timedelta(seconds=1))
    async with client_for(app) as client:
        response = await client.post("/auth/confirm", json={"email": email, "code": code})
    assert response.status_code == 400 and await status(app, user_id) == "unverified"


async def test_new_code_waits_a_minute_replaces_old_and_has_hourly_limit(database_app):
    app = database_app
    user_id, _, _ = await unverified(app)
    first = await app.state.auth.send_code(user_id, CONFIRM_PURPOSE)
    assert first and await app.state.auth.send_code(user_id, CONFIRM_PURPOSE) is None
    codes = [first]
    for _ in range(CODE_WINDOW_LIMIT - 1):
        await shift(app, user_id, CONFIRM_PURPOSE, sent_at=CODE_RESEND)
        codes.append(await app.state.auth.send_code(user_id, CONFIRM_PURPOSE))
    assert all(codes)
    await shift(app, user_id, CONFIRM_PURPOSE, sent_at=CODE_RESEND)
    assert await app.state.auth.send_code(user_id, CONFIRM_PURPOSE) is None
    await shift(app, user_id, CONFIRM_PURPOSE, sent_at=CODE_RESEND, window_started_at=timedelta(hours=1, seconds=1))
    latest = await app.state.auth.send_code(user_id, CONFIRM_PURPOSE)
    assert latest == first or await app.state.auth.confirm(user_id, first) == "invalid"
    assert await app.state.auth.confirm(user_id, latest) == "confirmed"


async def test_reset_by_code_revokes_sessions_and_rejects_reuse(database_app):
    app = database_app
    user_id, alumni_id = await fixture(app)
    email = user_id + "@example.test"
    session = app.state.auth.session(alumni_id, user_id)
    code = await app.state.auth.send_code(user_id, RESET_PURPOSE)
    body = {"email": email, "code": code, "password": "synthetic-new-password"}
    async with client_for(app) as client:
        stranger = await client.post("/auth/reset", json={**body, "email": "nobody@example.test"})
        reset = await client.post("/auth/reset", json=body)
        again = await client.post("/auth/reset", json=body)
        old = await client.get("/me", headers={"authorization": "Bearer " + session})
        login = await client.post("/auth/login", json={"email": email, "password": body["password"]})
    assert stranger.status_code == 400 and stranger.json()["error"] == CODE_INVALID
    assert reset.json() == {"ok": True} and again.status_code == 400
    assert old.status_code == 401 and login.status_code == 200


async def test_confirmation_code_does_not_reset_password(database_app):
    app = database_app
    user_id, _ = await fixture(app)
    code = await app.state.auth.send_code(user_id, CONFIRM_PURPOSE)
    assert await app.state.auth.reset(user_id, "synthetic-new-password", code) == "invalid"


async def test_forgot_sends_code_letter(database_app, monkeypatch):
    app = database_app
    user_id, _ = await fixture(app)
    sent = []

    async def capture(to, subject, text):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(app.state.settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(app.state.notifications, "send_email", capture)
    async with client_for(app) as client:
        first = await client.post("/auth/forgot", json={"email": user_id + "@example.test"})
        second = await client.post("/auth/forgot", json={"email": user_id + "@example.test"})
        unknown = await client.post("/auth/forgot", json={"email": "nobody@example.test"})
    assert first.json() == second.json() == unknown.json() == {"ok": True}
    [letter] = sent
    assert letter["subject"].startswith("Код для восстановления пароля")
    code = letter["text"].split("Код: ", 1)[1][:6]
    assert await app.state.auth.reset(user_id, "synthetic-new-password", code) == "reset"


async def test_deleted_profile_loses_codes(database_app):
    app = database_app
    user_id, alumni_id = await fixture(app)
    await app.state.auth.send_code(user_id, RESET_PURPOSE)
    await anonymize(app.state, alumni_id)
    assert await app.state.database.rows("SELECT id FROM club_email_codes WHERE user_id=%s", (user_id,)) == []
