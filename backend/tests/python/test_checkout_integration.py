import asyncio
from uuid import uuid4

import httpx
import pytest

from club_api.core.errors import ApiError
from club_api.modules.checkout.payments import payment_outcome
from club_api.modules.notifications.mail import CLUB_NAME


def contact():
    return {
        "contact_fio": "Тестовый заявитель",
        "contact_phone": "+70000000000",
        "contact_email": "synthetic@example.test",
        "fulfillment": "pickup",
        "consent_pdn": True,
    }


async def product(app, stock=10):
    return await app.state.store.create(
        "products",
        {"slug": str(uuid4()), "title": "Тестовый товар", "price": 5000, "stock": stock, "status": "published"},
    )


async def fill(client, product, session, qty=1):
    response = await client.post(
        "/cart",
        headers={"x-cart-session": session},
        json={"type": "merch", "ref_id": product["slug"], "qty": qty, "price": 1},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["price"] == product["price"]


@pytest.mark.asyncio
async def test_checkout_replay_stock_and_single_release(database_app):
    app = database_app
    row = await product(app)
    session, key = str(uuid4()), str(uuid4())
    headers = {"x-cart-session": session, "idempotency-key": key}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await fill(client, row, session, 2)
        replies = await asyncio.gather(*(client.post("/orders", headers=headers, json=contact()) for _ in range(2)))
        assert all(reply.status_code == 200 for reply in replies)
        assert replies[0].json()["number"] == replies[1].json()["number"]
        assert replies[0].json()["total_estimate"] == 10000
        assert (await app.state.store.one("products", row["id"]))["stock"] == 8
        orders = await app.state.store.read("orders", limit=-1)
        assert len(orders) == 1
        assert (await client.get("/cart", headers=headers)).json()["items"] == []
        changed = {**contact(), "comment": "Другой запрос"}
        assert (await client.post("/orders", headers=headers, json=changed)).status_code == 409
        assert await app.state.checkout.change_status(orders[0]["id"], "canceled")
        assert not await app.state.checkout.change_status(orders[0]["id"], "canceled")
        assert (await app.state.store.one("products", row["id"]))["stock"] == 10
        with pytest.raises(ApiError):
            await app.state.checkout.change_status(orders[0]["id"], "new")


@pytest.mark.asyncio
async def test_two_carts_cannot_oversell_last_stock(database_app):
    app = database_app
    row = await product(app, stock=1)
    sessions = [str(uuid4()), str(uuid4())]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for session in sessions:
            await fill(client, row, session)
        responses = await asyncio.gather(
            *(client.post("/orders", headers={"x-cart-session": session}, json=contact()) for session in sessions)
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        assert (await app.state.store.one("products", row["id"]))["stock"] == 0
        assert len(await app.state.store.read("orders", limit=-1)) == 1


@pytest.mark.asyncio
async def test_price_and_profile_discount_revalidated(database_app):
    app = database_app
    row = await product(app, stock=1)
    session = str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await fill(client, row, session)
        cart = (await app.state.store.read("carts", filters={"session_token": {"_eq": session}}, limit=1))[0]
        base = {
            "alumni_id": None,
            "type": "merch",
            "items_json": cart["items_json"],
            "subtotal": 5000,
            "member_discount": 0,
            "total_estimate": 5000,
            **contact(),
            "status": "new",
            "payment_status": None,
        }
        await app.state.store.update("products", {"price": 10000}, id=row["id"])
        with pytest.raises(ApiError, match="Цена"):
            await app.state.checkout.commit(
                session=session,
                key=str(uuid4()),
                request_hash="synthetic",
                cart_id=cart["id"],
                cart_items=cart["items_json"],
                base=base,
            )
        assert (await app.state.store.one("products", row["id"]))["stock"] == 1
        assert not await app.state.store.read("orders", limit=-1)


@pytest.mark.asyncio
async def test_verified_webhook_is_idempotent_and_cannot_downgrade(database_app, monkeypatch):
    app = database_app
    mails = []

    async def capture(to, subject, text):
        mails.append({"subject": subject, "text": text})
        return True

    monkeypatch.setattr(app.state.notifications, "send_email", capture)
    app.state.settings.YOOKASSA_SHOP_ID = "synthetic-shop"
    from pydantic import SecretStr

    app.state.settings.YOOKASSA_SECRET_KEY = SecretStr("synthetic-provider-key")
    alumni = await app.state.store.create("alumni", {"fio": "Тестовый подписчик"})
    order = await app.state.store.create(
        "orders",
        {
            "number": "ALU-2026-000099",
            "alumni_id": alumni["id"],
            "contact_fio": "Тестовый подписчик",
            "contact_email": "subscriber@example.test",
            "type": "podcast",
            "status": "new",
            "payment_status": "pending",
            "total_estimate": 5000,
        },
    )
    provider = {
        "id": "synthetic-payment",
        "status": "succeeded",
        "paid": True,
        "amount": {"value": "50.00", "currency": "RUB"},
        "metadata": {"order_number": order["number"]},
    }
    requests = []

    def transport(request):
        requests.append(request.url.path)
        return httpx.Response(200, json=provider)

    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(transport)) as upstream,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("185.71.76.1", 3000)), base_url="http://test"
        ) as client,
    ):
        app.state.client = upstream
        body = {"event": "payment.succeeded", "object": {"id": provider["id"], "amount": {"value": "0.01"}}}
        replies = await asyncio.gather(*(client.post("/payments/yookassa/webhook", json=body) for _ in range(2)))
        assert all(reply.status_code == 200 for reply in replies)
        assert len(requests) == 2
        until = (await app.state.store.one("alumni", alumni["id"]))["podcast_sub_until"]
        assert until is not None
        assert {mail["subject"] for mail in mails} == {"Оплата по заявке ALU-2026-000099 получена - " + CLUB_NAME}
        assert mails[0]["text"].endswith("прошла успешно.\nПодписка на подкаст клуба действует 12 месяцев.")
        assert (await app.state.store.one("orders", order["id"]))["payment_status"] == "succeeded"
        provider["status"] = "canceled"
        assert (await client.post("/payments/yookassa/webhook", json=body)).status_code == 200
        assert (await app.state.store.one("orders", order["id"]))["payment_status"] == "succeeded"
        assert (await app.state.store.one("alumni", alumni["id"]))["podcast_sub_until"] == until
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.1", 3000)), base_url="http://test"
    ) as client:
        assert (
            await client.post("/payments/yookassa/webhook", headers={"x-forwarded-for": "185.71.76.1"}, json=body)
        ).status_code == 403


@pytest.mark.parametrize(
    ("value", "paid", "currency"),
    [("50.01", True, "RUB"), ("50", True, "RUB"), ("50.00", False, "RUB"), ("50.00", True, "USD")],
)
def test_mismatched_payment_requires_review(value, paid, currency):
    order = {"payment_id": None, "payment_status": "pending", "total_estimate": 5000, "status": "new"}
    assert (
        payment_outcome(
            order,
            {"id": "synthetic", "status": "succeeded", "paid": paid, "amount": {"value": value, "currency": currency}},
        )
        == "review"
    )
