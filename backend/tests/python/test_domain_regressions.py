import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_checkout_integration import contact, fill, product
from test_community_integration import account

from club_api.core.errors import ApiError
from club_api.domain import ACHIEVEMENTS
from club_api.jobs.tasks import retention


async def test_retention_scrubs_only_expired_orders_and_audit(database_app):
    state = database_app.state
    now = datetime.now(UTC)
    old = await state.store.create(
        "orders",
        {
            **contact(),
            "number": "OLD",
            "address": "Частный адрес",
            "comment": "Контакт",
            "created_at": now - timedelta(days=1100),
        },
    )
    recent = await state.store.create("orders", {**contact(), "number": "RECENT", "created_at": now})
    old_audit = await state.store.create("audit_log", {"event": "test", "created_at": now - timedelta(days=1100)})
    recent_audit = await state.store.create("audit_log", {"event": "test", "created_at": now})
    await retention(state)
    scrubbed = await state.store.one("orders", old["id"])
    assert scrubbed["contact_email"] == "-" and scrubbed["address"] is None and scrubbed["comment"] is None
    assert (await state.store.one("orders", recent["id"]))["contact_email"] == contact()["contact_email"]
    audit_ids = {row["id"] for row in await state.store.read("audit_log", fields=("id",))}
    assert old_audit["id"] not in audit_ids and recent_audit["id"] in audit_ids


async def test_completed_order_cannot_restore_consumed_stock(database_app):
    app = database_app
    row, session = await product(app, stock=1), str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await fill(client, row, session)
        response = await client.post("/orders", headers={"x-cart-session": session}, json=contact())
        assert response.status_code == 200
    order = (await app.state.store.read("orders", limit=1))[0]
    assert await app.state.checkout.change_status(order["id"], "done")
    for status in ("new", "canceled", "expired"):
        with pytest.raises(ApiError, match="Закрытую"):
            await app.state.checkout.change_status(order["id"], status)
    assert (await app.state.store.one("products", row["id"]))["stock"] == 0


async def test_money_above_signed_int32_round_trips_checkout(database_app):
    app = database_app
    _, headers = await account(app, "editor")
    session = str(uuid4())
    amount = 2147483648
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/admin/products",
            headers=headers,
            json={"title": "Дорогой товар", "category": "Тест", "price": amount, "stock": 2},
        )
        assert created.status_code == 200, created.text
        row = await app.state.store.one("products", created.json()["id"])
        assert row["price"] == amount
        await fill(client, row, session, 2)
        response = await client.post("/orders", headers={"x-cart-session": session}, json=contact())
        assert response.status_code == 200, response.text
        assert response.json()["subtotal"] == amount * 2 and response.json()["total_estimate"] == amount * 2


async def test_failed_guest_payment_releases_after_ttl_and_late_success_requires_review(database_app, monkeypatch):
    app = database_app
    state = app.state
    state.settings.YOOKASSA_SHOP_ID = "synthetic-shop"
    state.settings.YOOKASSA_SECRET_KEY = SecretStr("synthetic-provider-secret")
    state.settings.RESERVE_TTL_HOURS = 1

    async def failed_create(*_args):
        raise ApiError(502, "Временный сбой")

    monkeypatch.setattr(state.payments, "create", failed_create)
    row, session, key = await product(app, stock=1), str(uuid4()), str(uuid4())
    headers = {"x-cart-session": session, "idempotency-key": key}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await fill(client, row, session)
        result = await client.post("/orders", headers=headers, json=contact())
        assert result.status_code == 200 and "payment_url" not in result.json()
        assert (await client.post("/orders", headers=headers, json=contact())).json()["number"] == result.json()[
            "number"
        ]
    order = (await state.store.read("orders", limit=1))[0]
    assert order["payment_status"] is None and order["payment_id"] is None
    await state.store.update("orders", {"created_at": datetime.now(UTC) - timedelta(hours=2)}, id=order["id"])
    assert await state.checkout.expire_reservations() == 1
    assert (await state.store.one("products", row["id"]))["stock"] == 1
    result = await state.payments.apply(
        {
            "id": "synthetic-late-payment",
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "50.00", "currency": "RUB"},
            "metadata": {"order_number": order["number"]},
        }
    )
    assert result["outcome"] == "review"
    assert (await state.store.one("orders", order["id"]))["status"] == "expired"


async def test_payment_creation_failure_never_clears_known_or_succeeded_payment(database_app):
    state = database_app.state
    order = await state.store.create(
        "orders",
        {
            "number": "KNOWN",
            "status": "new",
            "payment_id": "synthetic-known-payment",
            "payment_status": "pending",
            "total_estimate": 5000,
        },
    )
    await state.payments.creation_failed(order["number"])
    assert (await state.store.one("orders", order["id"]))["payment_status"] == "pending"
    payment = {
        "id": "synthetic-known-payment",
        "status": "succeeded",
        "paid": True,
        "amount": {"value": "50.00", "currency": "RUB"},
        "metadata": {"order_number": order["number"]},
    }
    await asyncio.gather(state.payments.apply(payment), state.payments.creation_failed(order["number"]))
    assert (await state.store.one("orders", order["id"]))["payment_status"] == "succeeded"


async def test_attendance_failure_rolls_back_points_and_flag(database_app, monkeypatch):
    app = database_app
    alumni_id, _ = await account(app)
    _, headers = await account(app, "editor")
    event = await app.state.store.create("events", {"title": "Событие", "points": 75, "status": "published"})
    rsvp = await app.state.store.create("event_rsvps", {"event_id": event["id"], "alumni_id": alumni_id})
    original = app.state.store.update

    async def fail_flag(table, data, **kwargs):
        if table == "event_rsvps":
            raise ApiError(409, "Изменение отменено")
        return await original(table, data, **kwargs)

    monkeypatch.setattr(app.state.store, "update", fail_flag)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/admin/events/rsvp/{rsvp['id']}/attend", headers=headers)
    assert response.status_code == 409
    assert not await app.state.store.read("points_ledger", filters={"alumni_id": {"_eq": alumni_id}})
    assert not (await app.state.store.one("event_rsvps", rsvp["id"]))["attended"]
    assert (await app.state.store.one("alumni", alumni_id))["points_cached"] == 0


async def test_canceled_rsvp_cannot_receive_attendance_points(database_app, monkeypatch):
    app = database_app
    alumni_id, _ = await account(app)
    _, headers = await account(app, "editor")
    event = await app.state.store.create("events", {"title": "Событие", "points": 75, "status": "published"})
    rsvp = await app.state.store.create("event_rsvps", {"event_id": event["id"], "alumni_id": alumni_id})
    original = app.state.store.one
    canceled = False

    async def cancel_after_read(table, id, **kwargs):
        nonlocal canceled
        row = await original(table, id, **kwargs)
        if table == "event_rsvps" and not canceled:
            canceled = True
            await app.state.store.delete("event_rsvps", id=id)
        return row

    monkeypatch.setattr(app.state.store, "one", cancel_after_read)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/admin/events/rsvp/{rsvp['id']}/attend", headers=headers)
    assert canceled and response.status_code == 404
    assert not await app.state.store.read("points_ledger", filters={"alumni_id": {"_eq": alumni_id}})
    assert (await original("alumni", alumni_id))["points_cached"] == 0


async def test_decay_rechecks_activity_after_candidate_selection(database_app, monkeypatch):
    state = database_app.state
    alumni = await state.store.create("alumni", {"fio": "Активный участник"})
    await state.gamification.add(alumni["id"], reason="manual", delta=100)
    await state.store.update("alumni", {"last_activity_at": datetime.now(UTC) - timedelta(days=40)}, id=alumni["id"])
    original = state.store.read
    selected = False

    async def resume_activity(table, **kwargs):
        nonlocal selected
        rows = await original(table, **kwargs)
        if table == "alumni" and not selected:
            selected = True
            await state.gamification.add(alumni["id"], reason="manual", delta=100)
        return rows

    monkeypatch.setattr(state.store, "read", resume_activity)
    result = await state.gamification.decay()
    assert selected and result["affected"] == 0
    assert (await state.store.one("alumni", alumni["id"]))["points_cached"] == 200
    assert not await original("points_ledger", filters={"alumni_id": {"_eq": alumni["id"]}, "reason": {"_eq": "decay"}})


async def test_event_history_does_not_hide_upcoming_month_or_detail(database_app):
    app = database_app
    now = datetime.now(UTC)
    old = None
    for offset in range(60):
        row = await app.state.store.create(
            "events",
            {
                "title": f"Архивное событие {offset}",
                "starts_at": datetime(2020, 1, 1, tzinfo=UTC) + timedelta(days=offset),
                "status": "done",
            },
        )
        old = old or row
    future = await app.state.store.create(
        "events", {"title": "Будущая встреча", "starts_at": now + timedelta(days=2), "status": "published"}
    )
    draft = await app.state.store.create(
        "events", {"title": "Закрытый черновик", "starts_at": now + timedelta(days=1), "status": "draft"}
    )
    boundary = await app.state.store.create(
        "events", {"title": "Московская граница месяца", "starts_at": "2019-12-31T22:00:00Z", "status": "done"}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        listing = (await client.get("/events")).json()
        assert future["id"] in {row["id"] for row in listing} and old["id"] not in {row["id"] for row in listing}
        month = (await client.get("/events?month=2020-01")).json()
        assert old["id"] in {row["id"] for row in month} and boundary["id"] in {row["id"] for row in month}
        detail = (await client.get("/events", params={"id": old["id"]})).json()
        assert len(detail) == 1 and detail[0]["id"] == old["id"]
        assert (await client.get("/events", params={"id": draft["id"]})).json() == []
        for value in ("2020-13", "2020-1", "invalid"):
            assert (await client.get("/events", params={"month": value})).status_code == 400
        assert (await client.get("/events?id=invalid")).status_code == 400


async def test_merch_order_grants_first_order_without_adding_points(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    achievement = await app.state.store.create(
        "achievements", next(item for item in ACHIEVEMENTS if item["key"] == "first_order")
    )
    row, session = await product(app, stock=2), str(uuid4())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await fill(client, row, session)
        response = await client.post("/orders", headers={**headers, "x-cart-session": session}, json=contact())
        assert response.status_code == 200
        grants = await app.state.store.read("alumni_achievements", filters={"alumni_id": {"_eq": alumni_id}})
        assert [row["achievement_id"] for row in grants] == [achievement["id"]]
        profile = (await client.get("/me", headers=headers)).json()
        progress = next(item for item in profile["achievements"] if item["key"] == "first_order")
        assert progress["earned"] and progress["current"] == 1
    assert not await app.state.store.read("points_ledger", filters={"alumni_id": {"_eq": alumni_id}})
    assert (await app.state.store.one("alumni", alumni_id))["points_cached"] == 0


async def test_dpo_is_not_sold_and_guest_orders_do_not_grant_first_order(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    await app.state.store.create("achievements", next(item for item in ACHIEVEMENTS if item["key"] == "first_order"))
    program = await app.state.store.create(
        "programs", {"slug": str(uuid4()), "title": "Программа", "price": 10000, "status": "published"}
    )
    row = await product(app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = str(uuid4())
        added = await client.post(
            "/cart", headers={"x-cart-session": session}, json={"type": "dpo", "ref_id": program["slug"]}
        )
        assert added.status_code == 400
        assert (
            await client.post("/orders", headers={**headers, "x-cart-session": session}, json=contact())
        ).status_code == 400
        guest_session = str(uuid4())
        await fill(client, row, guest_session)
        assert (
            await client.post("/orders", headers={"x-cart-session": guest_session}, json=contact())
        ).status_code == 200
        profile = (await client.get("/me", headers=headers)).json()
        assert not next(item for item in profile["achievements"] if item["key"] == "first_order")["earned"]
    assert not await app.state.store.read("alumni_achievements", filters={"alumni_id": {"_eq": alumni_id}})
    assert not await app.state.store.read("points_ledger", filters={"alumni_id": {"_eq": alumni_id}})


async def test_first_order_keeps_legacy_progress_without_double_counting(database_app):
    state = database_app.state
    alumni = await state.store.create("alumni", {"fio": "Участник", "verification_status": "verified"})
    await state.store.create("orders", {"number": "LEGACY", "alumni_id": alumni["id"], "type": "merch"})
    ledger = [{"reason": "order"}]
    assert (await state.gamification.stats(alumni, ledger))["orders_count"] == 1
    assert (await state.gamification.stats(alumni, [*ledger, *ledger]))["orders_count"] == 2
