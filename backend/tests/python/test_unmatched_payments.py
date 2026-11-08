import httpx
import pytest
from test_donations import enable, letters, site

from club_api.domain import rubles

WEBHOOK = "/payments/yookassa/webhook"


def paid(payment_id, metadata, status="succeeded"):
    return {
        "id": payment_id,
        "status": status,
        "paid": status == "succeeded",
        "amount": {"value": "1500.00", "currency": "RUB"},
        "metadata": metadata,
    }


async def notify(app, payment):
    enable(app)
    app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payment)))
    async with site(app) as client:
        reply = await client.post(
            WEBHOOK, json={"event": "payment." + payment["status"], "object": {"id": payment["id"]}}
        )
    assert reply.status_code == 200, reply.text


async def unmatched_events(app):
    return await app.state.database.rows(
        "SELECT subject,detail FROM audit_log WHERE event=%s ORDER BY created_at", ("payment.unmatched",)
    )


def office_texts(sent):
    return [mail["text"] for mail in sent if mail["to"] == "club@example.test"]


@pytest.mark.parametrize(
    ("metadata", "subject", "target"),
    [
        ({"order_number": "ALU-2026-999999"}, "order:ALU-2026-999999", "по заявке ALU-2026-999999"),
        ({"donation_number": "DON-2026-999999"}, "donation:DON-2026-999999", "по пожертвованию DON-2026-999999"),
        ({}, None, "ни к одной заявке"),
    ],
)
async def test_paid_payment_without_record_reaches_audit_and_office(
    database_app, monkeypatch, metadata, subject, target
):
    app = database_app
    sent = letters(app, monkeypatch)
    await notify(app, paid("synthetic-unmatched", metadata))
    [event] = await unmatched_events(app)
    assert event["subject"] == subject and "synthetic-unmatched" in str(event["detail"])
    [text] = office_texts(sent)
    assert f"Платеж ЮKassa synthetic-unmatched на {rubles(150000)} прошел, но не зачтен {target}." in text


async def test_second_payment_for_paid_order_is_reported(database_app, monkeypatch):
    app = database_app
    sent = letters(app, monkeypatch)
    await app.state.store.create(
        "orders",
        {
            "number": "ALU-2026-000321",
            "type": "podcast",
            "status": "confirmed",
            "payment_id": "synthetic-first",
            "payment_status": "succeeded",
            "total_estimate": 150000,
        },
    )
    await notify(app, paid("synthetic-second", {"order_number": "ALU-2026-000321"}))
    [event] = await unmatched_events(app)
    assert event["subject"] == "order:ALU-2026-000321"
    assert len(office_texts(sent)) == 1


async def test_canceled_payment_without_record_is_not_reported(database_app, monkeypatch):
    app = database_app
    sent = letters(app, monkeypatch)
    await notify(app, paid("synthetic-canceled", {"order_number": "ALU-2026-999998"}, status="canceled"))
    assert await unmatched_events(app) == [] and office_texts(sent) == []
