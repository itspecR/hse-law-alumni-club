import re

import httpx
import pytest
from test_auth_integration import fixture
from test_mailing_consent import REGISTRATION

from club_api.modules.auth import service as auth_module
from club_api.modules.auth.service import DUPLICATE_ACCOUNT

OWNER_IP, ATTACKER_IP = "198.51.100.7", "203.0.113.9"
PASSWORD = "synthetic-old-password"


def client_from(app, address):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(address, 3000)), base_url="http://test")


async def accounts_with(app, email):
    rows = await app.state.database.rows("SELECT id FROM directus_users WHERE lower(email)=%s", (email,))
    return len(rows)


def enable_mail(app, monkeypatch):
    async def delivered(*_args):
        return True

    monkeypatch.setattr(app.state.settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(app.state.notifications, "send_email", delivered)


@pytest.mark.parametrize("mail", [False, True])
async def test_registration_on_staff_email_answers_like_a_new_one(database_app, monkeypatch, mail):
    app = database_app
    if mail:
        enable_mail(app, monkeypatch)
    await fixture(app)
    user_id, _ = await fixture(app, "editor")
    staff_email = user_id + "@example.test"
    async with client_from(app, OWNER_IP) as client:
        staff = await client.post("/auth/register", json={**REGISTRATION, "email": staff_email.upper()})
        fresh = await client.post("/auth/register", json={**REGISTRATION, "email": "fresh@example.test"})
    assert staff.status_code == fresh.status_code == 200
    assert staff.json() == fresh.json()
    assert await accounts_with(app, staff_email) == 1
    user = await app.state.auth.user(email=staff_email)
    assert user["role"] == "editor"
    events = await app.state.database.rows("SELECT event FROM audit_log WHERE actor=%s", ("email:" + staff_email,))
    assert [row["event"] for row in events] == ["register.concealed"]


async def test_registration_on_alumni_email_still_says_account_exists(database_app):
    app = database_app
    user_id, _ = await fixture(app)
    async with client_from(app, OWNER_IP) as client:
        reply = await client.post("/auth/register", json={**REGISTRATION, "email": user_id + "@example.test"})
    assert reply.status_code == 409 and reply.json()["error"] == DUPLICATE_ACCOUNT


async def test_failures_from_one_address_do_not_lock_the_owner_elsewhere(database_app):
    app = database_app
    user_id, _ = await fixture(app)
    email = user_id + "@example.test"
    for _ in range(app.state.auth.attempts.ACCOUNT_LIMIT):
        app.state.auth.attempts.fail(email, ATTACKER_IP)
    credentials = {"email": email, "password": PASSWORD}
    async with client_from(app, ATTACKER_IP) as attacker, client_from(app, OWNER_IP) as owner:
        blocked = await attacker.post("/auth/login", json=credentials)
        allowed = await owner.post("/auth/login", json=credentials)
    assert blocked.status_code == 429
    assert allowed.status_code == 200 and allowed.json()["token"]


async def test_every_login_costs_one_password_check(database_app, monkeypatch):
    app = database_app
    closed, _ = await fixture(app, status="suspended")
    sso, _ = await fixture(app, provider="external-sso")
    checks = []

    async def recorded(encoded, _password):
        checks.append(encoded)
        return False

    monkeypatch.setattr(auth_module, "verify_password", recorded)
    for email in ("nobody@example.test", closed + "@example.test", sso + "@example.test"):
        assert (await app.state.auth.authenticate(email, PASSWORD, scope="alumni")) == ("invalid", None)
    assert checks == [None, None, None]


def guard(app, email):
    for index in range(app.state.auth.attempts.GUARD_LIMIT):
        app.state.auth.attempts.fail(email, f"192.0.2.{index}")


def capture_mail(app, monkeypatch):
    sent = []

    async def delivered(to, subject, text):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(app.state.settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(app.state.notifications, "send_email", delivered)
    return sent


async def test_guarded_email_answers_alike_and_mails_code_only_for_right_password(database_app, monkeypatch):
    app = database_app
    sent = capture_mail(app, monkeypatch)
    user_id, _ = await fixture(app)
    email = user_id + "@example.test"
    guard(app, email)
    guard(app, "nobody@example.test")
    async with client_from(app, OWNER_IP) as client:
        wrong = await client.post("/auth/login", json={"email": email, "password": "wrong-password"})
        missing = await client.post("/auth/login", json={"email": "nobody@example.test", "password": PASSWORD})
        assert sent == []
        right = await client.post("/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert wrong.status_code == missing.status_code == right.status_code == 200
    assert wrong.json() == missing.json() == right.json() == {"code_required": True}
    assert [mail["to"] for mail in sent] == [email]
    code = re.search(r"\b\d{6}\b", sent[0]["text"]).group()
    async with client_from(app, OWNER_IP) as client:
        bad = await client.post(
            "/auth/login-code", json={"email": email, "code": str((int(code) + 1) % 10**6).zfill(6)}
        )
        good = await client.post("/auth/login-code", json={"email": email, "code": code})
        reused = await client.post("/auth/login-code", json={"email": email, "code": code})
    assert bad.status_code == reused.status_code == 400
    assert good.status_code == 200 and good.json()["token"]


async def test_guarded_email_without_mail_closes_password_login(database_app):
    app = database_app
    user_id, _ = await fixture(app)
    email = user_id + "@example.test"
    guard(app, email)
    async with client_from(app, OWNER_IP) as client:
        reply = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 429


async def test_guard_leaves_staff_login_to_its_own_code(database_app):
    app = database_app
    user_id, _ = await fixture(app, "editor")
    email = user_id + "@example.test"
    guard(app, email)
    async with client_from(app, OWNER_IP) as client:
        reply = await client.post("/auth/admin-login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200 and reply.json()["token"]
