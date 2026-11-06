import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_auth_integration import fixture
from test_portfolio_applications import solved_captcha

from club_api.jobs.tasks import retention
from club_api.modules.donations.routes import DONATION_MIN_RUB
from club_api.modules.donations.service import DONATION_CONSENTS, donation_outcome
from club_api.modules.members.service import anonymize
from club_api.modules.notifications.mail import CLUB_NAME

PAYMENT_ID = "synthetic-donation-payment"
PAYMENT_URL = "https://yoomoney.ru/checkout/payments/v2/contract?orderId=synthetic"
BODY = {
    "amount": 3000,
    "email": "Donor@Example.test",
    "name": "Вымышленный Даритель",
    "consent_offer": True,
    "consent_pdn": True,
}


class Provider:
    def __init__(self):
        self.payment = None
        self.sent = []

    def __call__(self, request):
        if request.method == "POST":
            body = json.loads(request.content)
            self.sent.append({"body": body, "key": request.headers.get("Idempotence-Key")})
            self.payment = {
                "id": PAYMENT_ID,
                "status": "pending",
                "paid": False,
                "amount": body["amount"],
                "metadata": body["metadata"],
                "confirmation": {"type": "redirect", "confirmation_url": PAYMENT_URL},
            }
        return httpx.Response(200, json=self.payment)

    def finish(self, status, value=None):
        self.payment = {**self.payment, "status": status, "paid": status == "succeeded"}
        if value:
            self.payment["amount"] = {"value": value, "currency": "RUB"}


def enable(app):
    app.state.settings.YOOKASSA_SHOP_ID = "synthetic-shop"
    app.state.settings.YOOKASSA_SECRET_KEY = SecretStr("synthetic-provider-key")


def site(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("185.71.76.1", 3000)), base_url="http://test"
    )


def letters(app, monkeypatch):
    sent = []

    async def capture(to, subject, text):
        sent.append({"to": to, "subject": subject, "text": text})
        return True

    monkeypatch.setattr(app.state.notifications, "send_email", capture)
    monkeypatch.setattr(app.state.settings, "OFFICE_NOTIFY_CHANNEL", "email")
    monkeypatch.setattr(app.state.settings, "OFFICE_EMAIL", "club@example.test")
    return sent


async def donate(client, body=BODY):
    return await client.post("/donations", json={**body, "captcha": await solved_captcha(client)})


async def donation(app, number):
    row = (await app.state.database.rows("SELECT * FROM club_donations WHERE number=%s", (number,)))[0]
    consents = row["consents"]
    return {**row, "consents": json.loads(consents) if isinstance(consents, str) else consents}


async def started(app, client, provider):
    enable(app)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app.state.client = upstream
    reply = await donate(client)
    assert reply.status_code == 200, reply.text
    return reply.json()["number"]


async def webhook(client):
    return await client.post(
        "/payments/yookassa/webhook", json={"event": "payment.succeeded", "object": {"id": PAYMENT_ID}}
    )


async def test_donation_starts_payment_without_receipt(database_app):
    app, provider = database_app, Provider()
    async with site(app) as client:
        number = await started(app, client, provider)
    [sent] = provider.sent
    assert re.fullmatch(r"DON-\d{4}-000001", number)
    assert sent["body"]["amount"] == {"value": "3000.00", "currency": "RUB"} and "receipt" not in sent["body"]
    assert sent["body"]["metadata"] == {"donation_number": number}
    assert sent["body"]["confirmation"]["return_url"].endswith("/give/thanks")
    assert sent["key"] and sent["body"]["description"] == "Пожертвование на развитие факультета права НИУ ВШЭ"
    row = await donation(app, number)
    assert (row["amount"], row["email"], row["name"]) == (300000, "donor@example.test", "Вымышленный Даритель")
    assert (row["status"], row["payment_id"], row["period"]) == ("pending", PAYMENT_ID, "once")
    assert row["consents"] == DONATION_CONSENTS


async def test_paid_donation_thanks_donor_once_and_keeps_status(database_app, monkeypatch):
    app, provider = database_app, Provider()
    sent = letters(app, monkeypatch)
    async with site(app) as client:
        number = await started(app, client, provider)
        provider.finish("succeeded")
        assert (await webhook(client)).status_code == 200
        assert (await webhook(client)).status_code == 200
        provider.finish("canceled")
        assert (await webhook(client)).status_code == 200
    row = await donation(app, number)
    assert row["status"] == "succeeded" and row["paid_at"] is not None
    donor = [mail for mail in sent if mail["to"] == "donor@example.test"]
    office = [mail for mail in sent if mail["to"] == "club@example.test"]
    assert len(donor) == 1 and len(office) == 1
    assert donor[0]["subject"] == "Спасибо за пожертвование - " + CLUB_NAME
    assert "Здравствуйте, Вымышленный Даритель!" in donor[0]["text"] and number in donor[0]["text"]
    assert "3 000 ₽ на развитие факультета права НИУ ВШЭ" in donor[0]["text"]
    assert number in office[0]["text"]
    events = await app.state.database.rows("SELECT event FROM audit_log WHERE subject=%s", ("donation:" + number,))
    assert sorted(row["event"] for row in events) == ["donation.created", "donation.succeeded"]


@pytest.mark.parametrize(
    ("status", "value", "expected"),
    [("succeeded", "2999.00", "review"), ("canceled", None, "canceled"), ("waiting_for_capture", None, "pending")],
)
async def test_wrong_amount_goes_to_review_and_cancel_closes(database_app, monkeypatch, status, value, expected):
    app, provider = database_app, Provider()
    sent = letters(app, monkeypatch)
    async with site(app) as client:
        number = await started(app, client, provider)
        provider.finish(status, value)
        assert (await webhook(client)).status_code == 200
    assert (await donation(app, number))["status"] == expected
    assert sent == []


async def test_donation_requires_payments_consents_and_bounds(database_app):
    app = database_app
    async with site(app) as client:
        disabled = await client.post("/donations", json=BODY)
        enable(app)
        no_offer = await donate(client, {**BODY, "consent_offer": False})
        no_pdn = await donate(client, {**BODY, "consent_pdn": False})
        small = await donate(client, {**BODY, "amount": DONATION_MIN_RUB - 1})
        no_captcha = await client.post("/donations", json=BODY)
    assert disabled.status_code == 503
    assert {no_offer.status_code, no_pdn.status_code, small.status_code, no_captcha.status_code} == {400}
    assert await app.state.database.rows("SELECT id FROM club_donations") == []


async def test_failed_payment_start_marks_donation(database_app):
    app = database_app
    enable(app)
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, json={})))
    async with site(app) as client:
        reply = await donate(client)
    [row] = await app.state.database.rows("SELECT status,payment_id FROM club_donations")
    assert reply.status_code == 502 and (row["status"], row["payment_id"]) == ("failed", None)


def test_outcome_ignores_foreign_payment_and_repeats():
    pending = {"payment_id": PAYMENT_ID, "status": "pending", "amount": 100}
    paid = {"id": PAYMENT_ID, "status": "succeeded", "paid": True, "amount": {"value": "1.00", "currency": "RUB"}}
    assert donation_outcome(pending, {**paid, "id": "other"}) == "ignored"
    assert donation_outcome({**pending, "status": "succeeded"}, paid) == "duplicate"
    assert donation_outcome(pending, paid) == "succeeded"
    assert donation_outcome({**pending, "status": "review"}, {**paid, "status": "canceled"}) == "ignored"


async def test_deleted_profile_and_retention_forget_donor(database_app):
    app = database_app
    _, alumni_id = await fixture(app)
    now = datetime.now(UTC)
    for number, alumni, created in (
        ("DON-2026-000001", alumni_id, now),
        ("DON-2020-000001", None, now - timedelta(days=app.state.settings.ORDER_RETENTION_DAYS + 1)),
    ):
        await app.state.database.execute(
            "INSERT INTO club_donations(id,number,amount,email,name,alumni_id,status,created_at) VALUES(%s,%s,10000,'donor@example.test','Даритель',%s,'succeeded',%s)",
            (str(uuid4()), number, alumni, created),
        )
    await anonymize(app.state, alumni_id)
    await retention(app.state)
    rows = await app.state.database.rows("SELECT email,name,alumni_id,status FROM club_donations ORDER BY number")
    assert [(row["email"], row["name"], row["alumni_id"]) for row in rows] == [("-", None, None), ("-", None, None)]
    assert {row["status"] for row in rows} == {"succeeded"}


async def test_sitemap_lists_give_page(database_app):
    async with site(database_app) as client:
        sitemap = (await client.get("/sitemap.xml")).text
    assert "/give</loc>" in sitemap
