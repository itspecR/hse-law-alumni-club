import logging
from uuid import uuid4

import httpx
from test_checkout_integration import contact, fill, place, product
from test_community_integration import account

from club_api.modules.auth.service import ADMIN_CHECK_FAILED, ALUMNI_CHECK_FAILED, SESSION_CHECK_FAILED

ALWAYS = float("inf")


def browser(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


def flaky(monkeypatch, auth, name, failures):
    original = getattr(auth, name)
    calls = []

    async def check(payload):
        calls.append(payload)
        if len(calls) <= failures:
            raise ConnectionResetError("synthetic-connection-detail")
        return await original(payload)

    monkeypatch.setattr(auth, name, check)
    return calls


def refused(response):
    return response.status_code == 503 and response.json()["error"] == SESSION_CHECK_FAILED


async def test_one_failed_session_check_is_retried_and_logged_without_details(database_app, monkeypatch, caplog):
    app = database_app
    _, member = await account(app)
    calls = flaky(monkeypatch, app.state.auth, "alumni_session", 1)
    with caplog.at_level(logging.WARNING, logger="club.auth"):
        async with browser(app) as client:
            response = await client.get("/me", headers=member)
    assert response.status_code == 200 and response.json()["alumni"]["fio"]
    assert len(calls) >= 2 and calls[0] == calls[1]
    assert ALUMNI_CHECK_FAILED + ": ConnectionResetError" in caplog.text
    assert "synthetic-connection-detail" not in caplog.text


async def test_failed_session_check_is_never_treated_as_guest(database_app, monkeypatch):
    app = database_app
    _, member = await account(app)
    row = await product(app)
    session = str(uuid4())
    async with browser(app) as client:
        await fill(client, row, session)
        calls = flaky(monkeypatch, app.state.auth, "alumni_session", ALWAYS)
        for path in ("/me", "/events", "/podcasts"):
            assert refused(await client.get(path, headers=member)), path
        assert refused(await client.get("/cart", headers={"x-cart-session": session, **member}))
        order = await place(
            client, headers={"x-cart-session": session, "idempotency-key": str(uuid4()), **member}, json=contact()
        )
        assert refused(order)
        assert (await client.get("/events")).status_code == 200
    assert await app.state.store.read("orders", limit=-1) == []
    assert len(calls) == 2 * 5


async def test_invalid_sessions_still_answer_401_without_retry(database_app, monkeypatch):
    app = database_app
    alumni_id, member = await account(app)
    calls = flaky(monkeypatch, app.state.auth, "alumni_session", 0)
    await app.state.database.execute("UPDATE alumni SET token_version=token_version+1 WHERE id=%s", (alumni_id,))
    async with browser(app) as client:
        assert (await client.get("/me", headers=member)).status_code == 401
        for token in ("broken", app.state.auth.token({"alumni_id": "not-a-uuid", "sub": "x"}, 60)):
            assert (await client.get("/me", headers={"Authorization": "Bearer " + token})).status_code == 401
    assert len(calls) == 1


async def test_failed_staff_check_answers_503_and_recovers(database_app, monkeypatch, caplog):
    app = database_app
    _, admin = await account(app, "admin")
    with caplog.at_level(logging.WARNING, logger="club.auth"):
        async with browser(app) as client:
            calls = flaky(monkeypatch, app.state.auth, "admin_session", ALWAYS)
            assert refused(await client.get("/auth/admin-session", headers=admin))
            assert refused(await client.get("/admin/members", headers=admin))
            monkeypatch.undo()
            restored = await client.get("/auth/admin-session", headers=admin)
    assert restored.status_code == 200 and restored.json()["role"] == "admin"
    assert len(calls) == 4 and ADMIN_CHECK_FAILED + ": ConnectionResetError" in caplog.text
