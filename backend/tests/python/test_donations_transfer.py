import base64
from uuid import uuid4

import pytest
from test_community_integration import account
from test_donations import BODY, donate, donation, enable, letters, site
from test_donations_admin import client as admin_client
from test_requisites import PAYEE

from club_api.domain import rubles
from club_api.modules.notifications.mail import CLUB_NAME

TRANSFER_BODY = {**BODY, "method": "transfer"}


def enable_transfer(app, monkeypatch):
    for name, value in PAYEE.items():
        monkeypatch.setattr(app.state.settings, name, value)


async def config(client):
    reply = (await client.get("/donations/config")).json()
    return reply["enabled"], reply["card"], reply["transfer"]


async def test_config_reports_each_available_method(database_app, monkeypatch):
    app = database_app
    async with site(app) as client:
        nothing = await config(client)
        enable_transfer(app, monkeypatch)
        transfer = await config(client)
        enable(app)
        both = await config(client)
    assert (nothing, transfer, both) == ((False, False, False), (True, False, True), (True, True, True))


async def test_transfer_donation_returns_qr_and_mails_requisites(database_app, monkeypatch):
    app = database_app
    sent = letters(app, monkeypatch)
    enable_transfer(app, monkeypatch)
    async with site(app) as client:
        reply = await donate(client, TRANSFER_BODY)
    assert reply.status_code == 200, reply.text
    number, transfer = reply.json()["number"], reply.json()["transfer"]
    assert "payment_url" not in reply.json()
    purpose = f"Пожертвование {number} на развитие факультета права НИУ ВШЭ"
    assert (transfer["amount"], transfer["purpose"]) == (300000, purpose)
    assert [item["value"] for item in transfer["payee"]] == list(PAYEE.values())
    assert [item["label"] for item in transfer["payee"]][:3] == ["Получатель", "ИНН", "КПП"]
    assert transfer["qr_svg"].startswith("data:image/svg+xml")
    assert base64.b64decode(transfer["qr_png"].removeprefix("data:image/png;base64,")).startswith(b"\x89PNG")
    row = await donation(app, number)
    assert (row["status"], row["method"], row["payment_id"], row["paid_at"]) == ("awaiting", "transfer", None, None)
    [donor] = [mail for mail in sent if mail["to"] == "donor@example.test"]
    [office] = [mail for mail in sent if mail["to"] == "club@example.test"]
    assert donor["subject"] == "Реквизиты для пожертвования - " + CLUB_NAME
    assert "Здравствуйте, Вымышленный Даритель!" in donor["text"]
    assert f"Расчетный счет: {PAYEE['PAYEE_ACCOUNT']}" in donor["text"]
    assert f"Сумма: {rubles(300000)}" in donor["text"] and f"Назначение платежа: {purpose}" in donor["text"]
    assert number in office["text"] and "ждет перевода" in office["text"]
    events = await app.state.database.rows("SELECT event FROM audit_log WHERE subject=%s", ("donation:" + number,))
    assert [row["event"] for row in events] == ["donation.created"]


@pytest.mark.parametrize(("card", "transfer", "method"), [(True, False, "transfer"), (False, True, "card")])
async def test_unavailable_method_is_refused_before_saving(database_app, monkeypatch, card, transfer, method):
    app = database_app
    if card:
        enable(app)
    if transfer:
        enable_transfer(app, monkeypatch)
    async with site(app) as client:
        reply = await donate(client, {**BODY, "method": method})
    assert reply.status_code == 503 and reply.json()["error"] == "Этот способ оплаты пока не подключен."
    assert await app.state.database.rows("SELECT id FROM club_donations") == []


async def test_unknown_method_is_rejected(database_app, monkeypatch):
    app = database_app
    enable_transfer(app, monkeypatch)
    async with site(app) as client:
        reply = await donate(client, {**BODY, "method": "cash"})
    assert reply.status_code == 400
    assert await app.state.database.rows("SELECT id FROM club_donations") == []


async def awaiting_transfer(app, monkeypatch):
    enable_transfer(app, monkeypatch)
    async with site(app) as client:
        reply = await donate(client, TRANSFER_BODY)
    assert reply.status_code == 200, reply.text
    return reply.json()["number"]


async def test_admin_marks_transfer_received_once_and_donor_is_thanked(database_app, monkeypatch):
    app = database_app
    sent = letters(app, monkeypatch)
    number = await awaiting_transfer(app, monkeypatch)
    _, headers = await account(app, "admin")
    async with admin_client(app) as http:
        first = await http.post(f"/admin/donations/{number}/received", headers=headers)
        second = await http.post(f"/admin/donations/{number}/received", headers=headers)
        listed = await http.get("/admin/donations?range=all", headers=headers)
    assert first.status_code == 200 and second.status_code == 409
    assert second.json()["error"] == "Это пожертвование не ждет перевода."
    row = await donation(app, number)
    assert (row["status"], row["method"]) == ("succeeded", "transfer") and row["paid_at"] is not None
    thanks = [mail for mail in sent if mail["to"] == "donor@example.test" and mail["subject"].startswith("Спасибо")]
    received = [mail for mail in sent if mail["to"] == "club@example.test" and "получено" in mail["text"]]
    assert len(thanks) == 1 and number in thanks[0]["text"] and len(received) == 1
    [item] = listed.json()["items"]
    assert (item["number"], item["method"], item["status"]) == (number, "transfer", "succeeded")
    events = await app.state.database.rows(
        "SELECT event FROM audit_log WHERE subject=%s ORDER BY created_at", ("donation:" + number,)
    )
    assert sorted(row["event"] for row in events) == ["donation.created", "donation.transfer_received"]


async def test_only_awaiting_transfer_can_be_received_by_full_admin(database_app, monkeypatch):
    app = database_app
    number = await awaiting_transfer(app, monkeypatch)
    await app.state.database.execute(
        "INSERT INTO club_donations(id,number,amount,email,status,payment_id) VALUES(%s,%s,%s,%s,%s,%s)",
        (str(uuid4()), "DON-2026-000777", 100000, "card@example.test", "pending", "synthetic-pay"),
    )
    _, editor = await account(app, "editor")
    _, admin = await account(app, "admin")
    async with admin_client(app) as http:
        anonymous = await http.post(f"/admin/donations/{number}/received")
        forbidden = await http.post(f"/admin/donations/{number}/received", headers=editor)
        card = await http.post("/admin/donations/DON-2026-000777/received", headers=admin)
        missing = await http.post("/admin/donations/DON-2026-999999/received", headers=admin)
    assert (anonymous.status_code, forbidden.status_code, card.status_code, missing.status_code) == (401, 403, 409, 404)
    assert (await donation(app, number))["status"] == "awaiting"
    assert (await donation(app, "DON-2026-000777"))["status"] == "pending"
