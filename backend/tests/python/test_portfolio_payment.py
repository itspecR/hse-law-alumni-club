import hashlib
import re
from uuid import uuid4

import pytest
from test_community_integration import account
from test_donations_transfer import enable_transfer
from test_portfolio_applications import FORM, GROUP, PRICE, apply, client_for, mails, order, program
from test_requisites import PAYEE

from club_api.domain import rubles
from club_api.modules.portfolio.payment import payment_purpose
from club_api.modules.transfer.qr import PURPOSE_MAX

__all__ = ("mails",)
LINK = re.compile(r"/pay/([A-Za-z0-9_-]{43})\b")


async def applied(app, client, body=FORM, **extra):
    item = await program(app, **extra)
    reply = await apply(client, item["slug"], body)
    assert reply.status_code == 200, reply.text
    return await order(app, reply.json()["number"])


async def send(client, row, headers):
    return await client.post(f"/admin/orders/{row['id']}/transfer", headers=headers)


def link_token(mail):
    return LINK.search(mail["text"]).group(1)


async def stored(app, number):
    rows = await app.state.database.rows(
        "SELECT payment_status,transfer_token,transfer_sent_at,status,paid_at FROM orders WHERE number=%s", (number,)
    )
    return rows[0]


async def test_editor_sends_requisites_and_link_shows_qr(database_app, mails, monkeypatch):
    app = database_app
    enable_transfer(app, monkeypatch)
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        row = await applied(app, client, GROUP)
        reply = await send(client, row, editor)
        letter = mails[-1]
        token = link_token(letter)
        page = await client.get(f"/transfers/{token}")
    assert reply.status_code == 200, reply.text
    purpose = f"Оплата участия в программе «Переговоры», заявка {row['number']}"
    assert letter["to"] == "applicant@example.test"
    assert letter["subject"].startswith(f"Реквизиты для оплаты по заявке {row['number']}")
    assert f"Расчетный счет: {PAYEE['PAYEE_ACCOUNT']}" in letter["text"]
    assert f"Сумма: {rubles(PRICE * 3)}" in letter["text"] and f"Назначение платежа: {purpose}" in letter["text"]
    saved = await stored(app, row["number"])
    assert saved["payment_status"] == "awaiting" and saved["transfer_sent_at"] is not None
    assert saved["transfer_token"] == hashlib.sha256(token.encode()).hexdigest() and token not in letter["subject"]
    body = page.json()
    assert page.status_code == 200 and (body["number"], body["program"], body["paid"]) == (
        row["number"],
        "Переговоры",
        False,
    )
    assert (body["transfer"]["amount"], body["transfer"]["purpose"]) == (PRICE * 3, purpose)
    assert body["transfer"]["qr_png"].startswith("data:image/png;base64,")
    events = await app.state.database.rows("SELECT event FROM audit_log WHERE subject=%s", ("order:" + row["number"],))
    assert "order.transfer_sent" in [event["event"] for event in events]


async def test_resent_requisites_replace_the_old_link(database_app, mails, monkeypatch):
    app = database_app
    enable_transfer(app, monkeypatch)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        row = await applied(app, client)
        await send(client, row, admin)
        first = link_token(mails[-1])
        assert (await send(client, row, admin)).status_code == 200
        second = link_token(mails[-1])
        old = await client.get(f"/transfers/{first}")
        new = await client.get(f"/transfers/{second}")
    assert first != second and old.status_code == 404 and new.status_code == 200


async def test_only_full_admin_marks_payment_received(database_app, mails, monkeypatch):
    app = database_app
    enable_transfer(app, monkeypatch)
    _, editor = await account(app, "editor")
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        row = await applied(app, client)
        await send(client, row, admin)
        token = link_token(mails[-1])
        forbidden = await client.post(f"/admin/orders/{row['id']}/transfer-received", headers=editor)
        received = await client.post(f"/admin/orders/{row['id']}/transfer-received", headers=admin)
        again = await client.post(f"/admin/orders/{row['id']}/transfer-received", headers=admin)
        page = await client.get(f"/transfers/{token}")
        resend = await send(client, row, admin)
    assert (forbidden.status_code, received.status_code, again.status_code) == (403, 200, 409)
    assert again.json()["error"] == "Заявка не ждет оплаты переводом."
    saved = await stored(app, row["number"])
    assert (saved["payment_status"], saved["status"]) == ("succeeded", "new") and saved["paid_at"] is not None
    assert page.json() == {"number": row["number"], "program": "Переговоры", "paid": True}
    assert resend.status_code == 409 and resend.json()["error"] == "Заявка уже оплачена."
    paid = [mail for mail in mails if mail["subject"].startswith(f"Оплата по заявке {row['number']} получена")]
    assert len(paid) == 1 and "прошла успешно" in paid[0]["text"]
    events = await app.state.database.rows("SELECT event FROM audit_log WHERE subject=%s", ("order:" + row["number"],))
    assert "order.transfer_received" in [event["event"] for event in events]


async def test_requisites_are_refused_when_they_cannot_be_paid(database_app, mails, monkeypatch):
    app = database_app
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        unpriced = await applied(app, client, price=0)
        disabled = await send(client, unpriced, admin)
        enable_transfer(app, monkeypatch)
        no_price = await send(client, unpriced, admin)
        canceled = await applied(app, client)
        await app.state.database.execute("UPDATE orders SET status='canceled' WHERE id=%s", (canceled["id"],))
        closed = await send(client, canceled, admin)
        merch = await app.state.store.create("orders", {"number": "ALU-2026-990001", "type": "merch", "status": "new"})
        other = await send(client, merch, admin)
        missing = await client.post(f"/admin/orders/{uuid4()}/transfer", headers=admin)
        anonymous = await client.post(f"/admin/orders/{unpriced['id']}/transfer")
    assert disabled.status_code == 503
    assert (no_price.status_code, no_price.json()["error"]) == (409, "Сначала укажите стоимость заявки.")
    assert (closed.status_code, closed.json()["error"]) == (409, "Заявка закрыта.")
    assert other.status_code == 409 and missing.status_code == 404 and anonymous.status_code == 401
    rows = await app.state.database.rows("SELECT COUNT(*) AS sent FROM orders WHERE transfer_token IS NOT NULL")
    assert rows[0]["sent"] == 0


async def test_payment_link_rejects_bad_unknown_and_closed(database_app, mails, monkeypatch):
    app = database_app
    enable_transfer(app, monkeypatch)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        row = await applied(app, client)
        await send(client, row, admin)
        token = link_token(mails[-1])
        await app.state.database.execute("UPDATE orders SET status='canceled' WHERE id=%s", (row["id"],))
        closed = await client.get(f"/transfers/{token}")
        unknown = await client.get("/transfers/" + "A" * 43)
        malformed = await client.get("/transfers/short")
    assert closed.status_code == 410 and closed.json()["error"] == "Заявка закрыта."
    assert unknown.status_code == 404 and malformed.status_code == 404


@pytest.mark.parametrize("title", ["Переговоры", "Очень длинное название программы " * 10])
def test_payment_purpose_fits_and_keeps_application_number(title):
    purpose = payment_purpose(title, "ALU-2026-000123")
    assert len(purpose) <= PURPOSE_MAX and purpose.endswith("», заявка ALU-2026-000123")
    assert purpose.startswith("Оплата участия в программе «" + title[:20])
