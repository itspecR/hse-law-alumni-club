import asyncio
import io
import wave
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_checkout_integration import contact, fill, product
from test_community_integration import account

from club_api.core.errors import ApiError


async def reserved_order(app, monkeypatch, *, variant=False):
    state = app.state
    state.settings.YOOKASSA_SHOP_ID = "synthetic-shop"
    state.settings.YOOKASSA_SECRET_KEY = SecretStr("synthetic-provider-key")
    payment = {"id": str(uuid4()), "status": "pending"}

    async def create(*_args):
        return payment

    monkeypatch.setattr(state.payments, "create", create)
    row, session = await product(app, stock=1), str(uuid4())
    if variant:
        await state.store.update("products", {"variants_json": [{"sku": "synthetic-size", "stock": 1}]}, id=row["id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        if variant:
            reply = await client.post(
                "/cart",
                headers={"x-cart-session": session},
                json={"type": "merch", "ref_id": row["slug"], "variant_sku": "synthetic-size"},
            )
            assert reply.status_code == 200
        else:
            await fill(client, row, session)
        reply = await client.post("/orders", headers={"x-cart-session": session}, json=contact())
        assert reply.status_code == 200
    order = (await state.store.read("orders", limit=1))[0]
    saved = await state.store.one("products", row["id"])
    assert (saved["variants_json"][0]["stock"] if variant else saved["stock"]) == 0
    return row, order, {**payment, "metadata": {"order_number": order["number"]}}


@pytest.mark.parametrize("variant", [False, True])
async def test_canceled_payment_releases_stock_once(database_app, monkeypatch, variant):
    state = database_app.state
    row, order, payment = await reserved_order(database_app, monkeypatch, variant=variant)
    payment["status"] = "canceled"
    await asyncio.gather(*(state.payments.apply(payment) for _ in range(2)))
    saved = await state.store.one("orders", order["id"])
    assert saved["status"] == "canceled" and saved["payment_status"] == "canceled"
    saved_product = await state.store.one("products", row["id"])
    assert (saved_product["variants_json"][0]["stock"] if variant else saved_product["stock"]) == 1
    payment.update(status="succeeded", paid=True, amount={"value": "50.00", "currency": "RUB"})
    assert (await state.payments.apply(payment))["outcome"] == "review"
    assert await state.store.one("products", row["id"]) == saved_product


async def test_expiry_preserves_order_taken_by_office(database_app, monkeypatch):
    state = database_app.state
    row, order, _ = await reserved_order(database_app, monkeypatch)
    state.settings.RESERVE_TTL_HOURS = 1
    await state.store.update(
        "orders", {"created_at": datetime.now(UTC) - timedelta(hours=2), "payment_status": "canceled"}, id=order["id"]
    )
    original = state.database.rows

    async def rows(query, params=()):
        result = await original(query, params)
        if "JOIN club_checkout_commits" in str(query):
            await state.checkout.change_status(order["id"], "in_progress")
        return result

    monkeypatch.setattr(state.database, "rows", rows)
    assert await state.checkout.expire_reservations() == 0
    assert (await state.store.one("orders", order["id"]))["status"] == "in_progress"
    assert (await state.store.one("products", row["id"]))["stock"] == 0


@pytest.mark.parametrize("outcome", ["canceled", "pending", "succeeded", "unavailable"])
async def test_expiry_reconciles_provider_before_releasing(database_app, monkeypatch, outcome):
    state = database_app.state
    row, order, payment = await reserved_order(database_app, monkeypatch)
    state.settings.RESERVE_TTL_HOURS = 1
    await state.store.update("orders", {"created_at": datetime.now(UTC) - timedelta(hours=2)}, id=order["id"])

    async def fetch(id):
        assert id == payment["id"]
        if outcome == "unavailable":
            raise ApiError(502, "Временный сбой")
        return {**payment, "status": outcome, "paid": True, "amount": {"value": "50.00", "currency": "RUB"}}

    monkeypatch.setattr(state.payments, "fetch", fetch)
    await state.checkout.expire_reservations()
    assert (await state.store.one("products", row["id"]))["stock"] == (1 if outcome == "canceled" else 0)
    saved = await state.store.one("orders", order["id"])
    assert saved["payment_status"] == ("pending" if outcome == "unavailable" else outcome)


@pytest.mark.parametrize("erase", ["self", "admin"])
@pytest.mark.parametrize("local", [False, True])
async def test_paid_audio_denies_deleted_profile(database_app, erase, local):
    app = database_app
    alumni_id, headers = await account(app)
    _, admin = await account(app, "admin")
    await app.state.store.update("alumni", {"podcast_sub_until": "2099-01-01T00:00:00Z"}, id=alumni_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        audio_url = "https://audio.example.test/test.mp3"
        if local:
            output = io.BytesIO()
            with wave.open(output, "wb") as sound:
                sound.setnchannels(1)
                sound.setsampwidth(2)
                sound.setframerate(8000)
                sound.writeframes(b"\x00\x00" * 80)
            upload = await client.post(
                "/admin/media", headers=admin, files={"file": ("audio.wav", output.getvalue(), "audio/wav")}
            )
            assert upload.status_code == 201
            audio_url = "/api/media/" + upload.json()["id"]
        for free in (False, True):
            await app.state.store.create(
                "podcasts", {"title": str(free), "is_free": free, "status": "published", "audio_url": audio_url}
            )
        items = (await client.get("/podcasts", headers=headers)).json()["items"]
        paid = next(item["audio_url"].removeprefix("/api") for item in items if not item["is_free"])
        free = next(item["audio_url"].removeprefix("/api") for item in items if item["is_free"])
        assert (await client.get(paid, headers={"range": "bytes=1-"})).status_code == (206 if local else 302)
        reply = (
            await client.post("/me/delete", headers=headers, json={"confirm": "УДАЛИТЬ"})
            if erase == "self"
            else await client.post(f"/admin/members/{alumni_id}/anonymize", headers=admin)
        )
        assert reply.status_code == 200
        assert (await client.get(paid)).status_code == 403
        await app.state.payments.extend_subscription(alumni_id)
        assert (await client.get(paid, headers={"range": "bytes=1-"})).status_code == 403
        assert (await client.get(free)).status_code == (200 if local else 302)


async def test_expiry_advances_past_unavailable_payments(database_app, monkeypatch):
    state = database_app.state
    state.settings.RESERVE_TTL_HOURS = 1
    state.settings.YOOKASSA_SHOP_ID = "synthetic-shop"
    state.settings.YOOKASSA_SECRET_KEY = SecretStr("synthetic-provider-key")
    row = await product(database_app, stock=0)
    for index in range(51):
        order = await state.store.create(
            "orders",
            {
                **contact(),
                "id": str(UUID(int=index + 1)),
                "number": f"BATCH-{index}",
                "created_at": datetime.now(UTC) - timedelta(hours=2),
                "payment_status": "pending" if index < 50 else None,
                "payment_id": f"synthetic-{index}" if index < 50 else None,
                "status": "new",
            },
        )
        await state.database.execute(
            "INSERT INTO club_checkout_commits(key_hash,request_hash,order_id,reservations,receipt) VALUES(%s,%s,%s,%s,%s)",
            (str(index), "synthetic", order["id"], [{"id": row["id"], "sku": None, "qty": 1}], {}),
        )
    monkeypatch.setattr(state.payments, "fetch", AsyncMock(side_effect=ApiError(502, "Временный сбой")))
    assert await state.checkout.expire_reservations() == 1
    assert (await state.store.one("products", row["id"]))["stock"] == 1
    assert (await state.store.one("orders", str(UUID(int=51))))["status"] == "expired"


@pytest.mark.parametrize("outcome", ["pending", "succeeded", "canceled", "missing", "unavailable"])
async def test_expiry_recovers_lost_payment_id(database_app, monkeypatch, outcome):
    state = database_app.state
    row, order, payment = await reserved_order(database_app, monkeypatch)
    state.settings.RESERVE_TTL_HOURS = 1
    await state.store.update(
        "orders", {"created_at": datetime.now(UTC) - timedelta(hours=2), "payment_id": None}, id=order["id"]
    )
    payment.update(status=outcome, paid=True, amount={"value": "50.00", "currency": "RUB"})
    calls = []

    async def lookup(url, *, params, auth):
        calls.append(dict(params))
        assert url == "https://api.yookassa.ru/v3/payments"
        if outcome == "unavailable":
            return httpx.Response(503, json={})
        if "cursor" not in params:
            return httpx.Response(200, json={"type": "list", "items": [], "next_cursor": "next-page"})
        return httpx.Response(200, json={"type": "list", "items": [] if outcome == "missing" else [payment]})

    monkeypatch.setattr(state.client, "get", lookup)
    notice = AsyncMock()
    monkeypatch.setattr(state.notifications, "office_text", notice)
    await state.checkout.expire_reservations()
    saved = await state.store.one("orders", order["id"])
    assert saved["payment_status"] == ("review" if outcome in ("missing", "unavailable") else outcome)
    assert saved["payment_id"] == (None if outcome in ("missing", "unavailable") else payment["id"])
    assert (await state.store.one("products", row["id"]))["stock"] == (1 if outcome == "canceled" else 0)
    if outcome in ("missing", "unavailable"):
        await state.checkout.expire_reservations()
        notice.assert_awaited_once()
        payment["status"] = "canceled"
        assert (await state.payments.apply(payment))["outcome"] == "canceled"
        assert (await state.store.one("products", row["id"]))["stock"] == 1
    else:
        assert len(calls) == 2
        notice.assert_not_awaited()


@pytest.mark.parametrize("invalid", ["duplicate", "cursor", "malformed"])
async def test_payment_lookup_rejects_ambiguous_or_incomplete_results(database_app, monkeypatch, invalid):
    state = database_app.state
    row, order, payment = await reserved_order(database_app, monkeypatch)
    payment["status"] = "canceled"
    payload = {"type": "list", "items": [payment]}
    if invalid == "duplicate":
        payload["items"].append({**payment, "id": str(uuid4())})
    elif invalid == "cursor":
        payload["next_cursor"] = "same-page"
    else:
        payload["items"] = [None]
    monkeypatch.setattr(state.client, "get", AsyncMock(return_value=httpx.Response(200, json=payload)))
    with pytest.raises(ApiError, match="сверить"):
        await state.payments.find_order_payment(order)
    assert (await state.store.one("products", row["id"]))["stock"] == 0
