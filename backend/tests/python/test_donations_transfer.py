import base64

import pytest
from test_donations import BODY, donate, donation, enable, letters, site
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
