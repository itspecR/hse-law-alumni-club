from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
from test_community_integration import account
from test_portfolio_applications import FORM, apply, order, program

from club_api.jobs.tasks import retention
from club_api.modules.notifications.mail import APPLICANT_MAIL_KIND, MAIL_RETENTION_DAYS


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def applicant_letters(state):
    return await state.database.rows(
        "SELECT to_addr,status,attempts FROM club_mail_outbox WHERE kind=%s ORDER BY id", (APPLICANT_MAIL_KIND,)
    )


async def test_application_letter_is_queued_and_retried(database_app, monkeypatch):
    app = database_app
    state = app.state
    item = await program(app)
    monkeypatch.setattr(state.notifications, "send_email", AsyncMock(return_value=False))
    async with client_for(app) as client:
        assert (await apply(client, item["slug"], FORM)).status_code == 200
    assert await applicant_letters(state) == [{"to_addr": FORM["contact_email"], "status": "pending", "attempts": 1}]
    due = datetime.now(UTC) - timedelta(minutes=1)
    await state.database.execute("UPDATE club_mail_outbox SET next_attempt_at=%s", (due,))
    monkeypatch.setattr(state.notifications, "send_email", AsyncMock(return_value=True))
    assert (await state.notifications.drain_mail())["sent"] == 1
    assert await applicant_letters(state) == [{"to_addr": FORM["contact_email"], "status": "sent", "attempts": 2}]


async def test_status_letter_goes_through_queue(database_app, monkeypatch):
    app = database_app
    state = app.state
    item = await program(app)
    _, admin = await account(app, "admin")
    monkeypatch.setattr(state.notifications, "send_email", AsyncMock(return_value=True))
    async with client_for(app) as client:
        number = (await apply(client, item["slug"], FORM)).json()["number"]
        row = await order(app, number)
        await client.patch("/admin/orders/" + row["id"], headers=admin, json={"status": "in_progress"})
    letters = await applicant_letters(state)
    assert [letter["status"] for letter in letters] == ["sent", "sent"]


async def test_retention_removes_only_old_finished_letters(database_app):
    state = database_app.state
    old = datetime.now(UTC) - timedelta(days=MAIL_RETENTION_DAYS + 1)
    fresh = datetime.now(UTC) - timedelta(days=MAIL_RETENTION_DAYS - 1)
    for status, created in (("sent", old), ("failed", old), ("pending", old), ("sent", fresh)):
        await state.database.execute(
            "INSERT INTO club_mail_outbox(kind,to_addr,subject,body,status,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (APPLICANT_MAIL_KIND, "person@example.test", "Тема", "Письмо", status, created),
        )
    await retention(state)
    rows = await state.database.rows("SELECT status FROM club_mail_outbox ORDER BY id")
    assert [row["status"] for row in rows] == ["pending", "sent"]
