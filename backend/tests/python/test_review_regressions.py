import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from test_community_integration import account, png

from club_api.core.errors import ApiError
from club_api.modules.catalog import sync
from club_api.modules.telegram.faq import program_to_bot, search


def catalog_page(items, *, total=6, page_size=3):
    data = {"items": items, "total": total, "pageSize": page_size}
    return "window.__INITIAL_STATE__=" + json.dumps(data) + ";window.__URQL_DATA__={};"


@pytest.mark.parametrize(
    "invalid", ["empty", "duplicate", "invalid_item", "changed_total", "changed_size", "missing_metadata"]
)
async def test_incomplete_hse_catalog_is_rejected(monkeypatch, invalid):
    cards = [{"id": index, "title": f"Программа {index}", "educationPricing": 100} for index in range(1, 7)]

    async def source(_state, url, **_kwargs):
        if "page=2" not in url:
            return catalog_page(cards[:3])
        items = cards[3:]
        if invalid == "empty":
            items = []
        elif invalid == "duplicate":
            items = cards[:3]
        elif invalid == "invalid_item":
            items = [None, *cards[4:]]
        if invalid == "missing_metadata":
            return 'window.__INITIAL_STATE__={"items":[]};window.__URQL_DATA__={};'
        return catalog_page(
            items, total=5 if invalid == "changed_total" else 6, page_size=2 if invalid == "changed_size" else 3
        )

    monkeypatch.setattr(sync, "get_html", source)
    with pytest.raises(ApiError) as failure:
        await sync.collect(SimpleNamespace(), sync.ACTUAL_URL)
    assert failure.value.status == 502


async def test_complete_catalog_accepts_partial_last_page(monkeypatch):
    cards = [{"id": index, "title": f"Программа {index}"} for index in range(1, 6)]

    async def source(_state, url, **_kwargs):
        return catalog_page(cards[3:] if "page=2" in url else cards[:3], total=5)

    monkeypatch.setattr(sync, "get_html", source)
    result = await sync.collect(SimpleNamespace(), sync.ACTUAL_URL)
    assert [card["hseId"] for card in result] == [str(index) for index in range(1, 6)]


async def test_first_mail_delivery_cannot_race_outbox_drain(database_app, monkeypatch):
    state = database_app.state
    started, resume = asyncio.Event(), asyncio.Event()
    deliveries = []

    async def deliver(*args):
        deliveries.append(args)
        started.set()
        await resume.wait()
        return True

    monkeypatch.setattr(state.notifications, "send_email", deliver)
    enqueue = asyncio.create_task(state.notifications.enqueue_mail("synthetic@example.test", "Тема", "Сообщение"))
    await asyncio.wait_for(started.wait(), 5)
    drain = asyncio.create_task(state.notifications.drain_mail())
    await asyncio.sleep(0.05)
    resume.set()
    result, _ = await asyncio.gather(enqueue, drain)
    saved = (await state.database.rows("SELECT status,attempts FROM club_mail_outbox WHERE id=%s", (result["id"],)))[0]
    assert len(deliveries) == 1
    assert saved == {"status": "sent", "attempts": 1}


async def test_mail_record_failure_does_not_send_again(database_app, monkeypatch):
    state = database_app.state
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(state.notifications, "send_email", sender)
    monkeypatch.setattr(state.notifications, "record_delivery", AsyncMock(side_effect=RuntimeError("synthetic")))
    result = await state.notifications.enqueue_mail("synthetic@example.test", "Тема", "Сообщение")
    assert result["sent"] and result["id"] is None
    sender.assert_awaited_once()
    assert not await state.database.rows("SELECT id FROM club_mail_outbox")


@pytest.mark.parametrize("role", ["admin", "editor"])
async def test_office_all_filters_are_optional_but_invalid_values_rejected(database_app, role):
    _, headers = await account(database_app, role)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=database_app), base_url="http://test") as client:
        for path in ("/admin/orders?status=&payment=", "/admin/members?status=&verification="):
            reply = await client.get(path, headers=headers)
            assert reply.status_code == 200, reply.text
        for path in ("/admin/orders?status=invalid", "/admin/orders?payment=invalid", "/admin/members?status=invalid"):
            assert (await client.get(path, headers=headers)).status_code == 400


async def test_cart_separates_same_slug_across_catalogs_and_keeps_legacy_unique_changes(database_app):
    state = database_app.state
    slug = "shared-catalog-slug"
    for table in ("products", "programs"):
        await state.store.create(
            table,
            {
                "title": table,
                "slug": slug,
                "price": 10000,
                "status": "published",
                **({"stock": 5} if table == "products" else {}),
            },
        )
    headers = {"x-cart-session": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=database_app), base_url="http://test") as client:
        for kind in ("dpo", "merch"):
            assert (await client.post("/cart", headers=headers, json={"type": kind, "ref_id": slug})).status_code == 200
        assert (await client.patch("/cart", headers=headers, json={"ref_id": slug, "qty": 0})).status_code == 409
        changed = await client.patch("/cart", headers=headers, json={"type": "merch", "ref_id": slug, "qty": 3})
        assert changed.status_code == 200
        assert {item["type"]: item["qty"] for item in changed.json()["items"]} == {"dpo": 1, "merch": 3}
        removed = await client.patch("/cart", headers=headers, json={"type": "merch", "ref_id": slug, "qty": 0})
        assert removed.status_code == 200 and [item["type"] for item in removed.json()["items"]] == ["dpo"]
        legacy = await client.patch("/cart", headers=headers, json={"ref_id": slug, "qty": 0})
        assert legacy.status_code == 200 and legacy.json()["items"] == []


async def test_done_event_cover_is_public_but_draft_cover_is_private(database_app):
    state = database_app.state
    _, headers = await account(database_app, "editor")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=database_app), base_url="http://test") as client:
        uploaded = await client.post("/admin/media", headers=headers, files={"file": ("cover.png", png(), "image/png")})
        assert uploaded.status_code == 201
        id = uploaded.json()["id"]
        event = await state.store.create(
            "events", {"title": "Событие", "starts_at": "2026-12-01T15:00:00Z", "cover": id, "status": "draft"}
        )
        assert (await client.get(f"/media/{id}")).status_code == 404
        await state.store.update("events", {"status": "done"}, id=event["id"])
        assert (await client.get(f"/media/{id}")).status_code == 200
        await state.store.update("events", {"cover": None}, id=event["id"])
        await state.store.create(
            "programs", {"title": "Черновик", "slug": str(uuid4()), "cover": id, "status": "draft"}
        )
        assert (await client.get(f"/media/{id}")).status_code == 404


async def test_early_subscription_renewal_stacks_once_and_reuses_pending_order(database_app):
    state = database_app.state
    id, headers = await account(database_app)
    initial = datetime(2099, 1, 15, tzinfo=UTC)
    await state.store.update("alumni", {"podcast_sub_until": initial}, id=id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=database_app), base_url="http://test") as client:
        replies = await asyncio.gather(*(client.post("/podcasts/subscribe", headers=headers) for _ in range(2)))
        assert all(reply.status_code == 200 for reply in replies)
        assert len({reply.json()["number"] for reply in replies}) == 1
        orders = await state.store.read("orders")
        assert len(orders) == 1
        assert (await state.store.one("alumni", id))["podcast_sub_until"].startswith("2099-01-15")
        payment = {
            "id": str(uuid4()),
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "4999.00", "currency": "RUB"},
            "metadata": {"order_number": orders[0]["number"]},
        }
        await asyncio.gather(*(state.payments.apply(payment) for _ in range(2)))
        assert (await state.store.one("alumni", id))["podcast_sub_until"].startswith("2100-01-15")


async def test_bot_finds_canonical_blended_program(database_app):
    state = database_app.state
    for format in ("blended", "online"):
        await state.store.create(
            "programs",
            {
                "title": "Программа права " + format,
                "slug": format,
                "format": format,
                "status": "published",
                "price": 10000,
            },
        )
    programs = [
        program_to_bot(row) for row in await state.store.read("programs", filters={"status": {"_eq": "published"}})
    ]
    found = search("смешанный право", programs)
    assert [item["url"] for item in found["programs"]] == ["/dpo/blended"]


@pytest.mark.parametrize("query", ["смешанный", "гибридный"])
def test_bot_format_aliases_filter_canonical_catalog(query):
    programs = [
        program_to_bot({"id": kind, "slug": kind, "title": "Программа права", "format": kind, "price": 10000})
        for kind in ("blended", "online")
    ]
    assert [item["url"] for item in search(query, programs)["programs"]] == ["/dpo/blended"]


@pytest.mark.parametrize("role", ["admin", "editor"])
async def test_product_editor_keeps_existing_images(database_app, role):
    state = database_app.state
    _, headers = await account(database_app, role)
    row = await state.store.create(
        "products",
        {
            "title": "Товар с фото",
            "slug": str(uuid4()),
            "status": "published",
            "price": 10000,
            "stock": 1,
            "images": ["https://example.test/image.jpg"],
        },
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=database_app), base_url="http://test") as client:
        reply = await client.get("/admin/products", headers=headers)
        assert reply.status_code == 200
        saved = reply.json()[0]
        assert saved["images"] == row["images"]
        assert (
            await client.patch(
                f"/admin/products/{row['id']}",
                headers=headers,
                json={"title": "Другое название", "images": saved["images"]},
            )
        ).status_code == 200
        assert (await state.store.one("products", row["id"]))["images"] == row["images"]
