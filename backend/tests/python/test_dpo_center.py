import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from test_community_integration import account

from club_api.core.errors import ApiError
from club_api.modules.catalog import center

SITE = "https://dpo.example.test"
CATALOG = SITE + center.CATALOG_PATH


def state_with(**settings):
    return SimpleNamespace(
        settings=SimpleNamespace(**{"DPO_SITE_CERTIFICATE": "", "DPO_SITE_LINK_URL": "", **settings})
    )


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), "orange").save(buffer, "JPEG")
    return buffer.getvalue()


def card(hse_id, title, **extra):
    return {
        "hse_id": hse_id,
        "title": title,
        "path": f"programs/programma-{hse_id}.html",
        "type_short": "ПК",
        "format": "online",
        "sphere": "corporate",
        "sphere_title": "Корпоративное право",
        "duration": "short",
        "price_sort": 45000,
        "start_sort": 0,
        "thumb": {"src": f"/media/catalog/{hse_id}.jpg", "webp": f"/media/catalog/{hse_id}.webp", "alt": title},
        "tags": [],
        "start": "Старт: 12 ноября 2026 г.",
        "price": "45 000 ₽",
        "compare": {
            "format": "Онлайн",
            "duration": "72 часа",
            "start": "",
            "modules": "",
            "teachers": "",
            "audience": "",
        },
        "high_rating": False,
        "application": {"form": None, "hse_url": ""},
        **extra,
    }


def test_center_card_becomes_club_program():
    assert center.map_card(card("101", "Корпоративные споры"), SITE) == {
        "hse_id": "101",
        "title": "Корпоративные споры",
        "direction": "Корпоративное право",
        "format": "online",
        "duration": "72 часа",
        "dates": {"start": "12 ноября 2026 г."},
        "price": 4500000,
        "document": "Удостоверение о повышении квалификации НИУ ВШЭ",
        "source_url": SITE + "/programs/programma-101.html",
        "cover_source": SITE + "/media/catalog/101.jpg",
    }
    other = center.map_card(
        card("A-7", "Медиация", format="mixed", type_short="ПП", start="", price="Цена по запросу", thumb=None), SITE
    )
    assert (other["format"], other["dates"], other["price"], other["cover_source"]) == ("blended", None, None, None)
    assert other["document"] == "Диплом о профессиональной переподготовке НИУ ВШЭ"
    assert center.map_card(card("102", "Право", start="Старт: ноябрь 2026 г."), SITE)["dates"] == {
        "start": "ноябрь 2026 г."
    }
    free = center.map_card(card("103", "Право", format="other", price="Бесплатно"), SITE)
    assert (free["format"], free["price"]) == (None, 0)
    foreign = card("104", "Право", thumb={"src": "https://evil.test/x.jpg"})
    assert center.map_card(foreign, SITE)["cover_source"] is None


@pytest.mark.parametrize(
    "broken",
    [
        {"hse_id": "1 2"},
        {"hse_id": ""},
        {"title": "  "},
        {"path": "../admin.html"},
        {"path": "https://evil.test/programs/x.html"},
    ],
)
def test_broken_center_card_is_skipped(broken):
    assert center.map_card({**card("101", "Право"), **broken}, SITE) is None
    assert center.map_card(None, SITE) is None


@pytest.mark.parametrize(
    "address,status",
    [
        ("", 409),
        ("http://dpo.example.test", 502),
        ("https://dpo.example.test:8443", 502),
        ("https://dpo.example.test/catalog", 502),
        ("https://user@dpo.example.test", 502),
        ("https://dpo.example.test:bad", 502),
    ],
)
def test_site_address_must_be_plain_https(address, status):
    with pytest.raises(ApiError) as failure:
        center.site_base(state_with(DPO_SITE_URL=address))
    assert failure.value.status == status


def test_site_address_keeps_only_origin():
    assert center.site_base(state_with(DPO_SITE_URL=" https://dpo.example.test/ ")) == SITE


@pytest.mark.parametrize(
    "address,base",
    [
        ("https://62.112.123.10:8443", "https://62.112.123.10:8443"),
        ("https://dpo.example.test:443/", SITE),
    ],
)
def test_site_address_keeps_port_only_with_certificate(address, base):
    assert center.site_base(state_with(DPO_SITE_URL=address, DPO_SITE_CERTIFICATE="c2VydA==")) == base


@pytest.mark.parametrize(
    "address,links",
    [
        ("", SITE),
        (" https://62.112.123.10:8443/ ", "https://62.112.123.10:8443"),
        ("https://dpo.example.test:443", SITE),
    ],
)
def test_card_links_use_public_address_when_set(address, links):
    assert center.link_base(state_with(DPO_SITE_LINK_URL=address), SITE) == links


@pytest.mark.parametrize("address", ["http://62.112.123.10:8443", "https://62.112.123.10:8443/catalog", "https://"])
def test_card_link_address_must_be_plain_https(address):
    with pytest.raises(ApiError) as failure:
        center.link_base(state_with(DPO_SITE_LINK_URL=address), SITE)
    assert (failure.value.status, failure.value.message) == (502, center.WRONG_LINK)


def test_card_links_and_covers_use_their_own_addresses():
    links = "https://62.112.123.10:8443"
    mapped = center.map_card(card("101", "Право"), SITE, links)
    assert mapped["source_url"] == links + "/programs/programma-101.html"
    assert mapped["cover_source"] == SITE + "/media/catalog/101.jpg"


@pytest.fixture
def dpo_center(database_app, monkeypatch):
    monkeypatch.setattr(database_app.state.settings, "DPO_SITE_URL", SITE)
    monkeypatch.setattr(center, "IMAGE_DELAY_SECONDS", 0)
    feed, calls = {"cards": []}, []

    async def handler(request):
        url = str(request.url)
        calls.append(url)
        if url == CATALOG:
            return httpx.Response(200, json=feed, headers={"content-type": "application/json"})
        if url.endswith(".jpg"):
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        return httpx.Response(404, text="")

    database_app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return database_app, feed, calls


async def programs(app):
    rows = await app.state.store.read(
        "programs",
        fields=("id", "slug", "title", "status", "catalog", "hse_id", "cover", "cover_source", "source_url", "price"),
        limit=-1,
    )
    return {row["slug"]: row for row in rows}


async def sync_run(app):
    return (await app.state.database.rows("SELECT * FROM club_dpo_sync WHERE id=1"))[0]


async def test_center_catalog_replaces_dpo_programs_and_keeps_known_rows(dpo_center):
    app, feed, calls = dpo_center
    store = app.state.store
    known = await store.create(
        "programs", {"slug": "old-slug", "title": "Старое", "hse_id": "101", "status": "published", "price": 100}
    )
    await store.create("programs", {"slug": "manual", "title": "Ручная", "status": "published", "price": 100})
    await store.create(
        "programs", {"slug": "club", "title": "Клуб", "status": "published", "price": 100, "catalog": "portfolio"}
    )
    feed["cards"] = [card("101", "Корпоративные споры"), card("102", "Медиация"), card("104", "Банкротство")]
    assert await center.sync_center(app.state) == {"found": 3, "created": 2, "updated": 1, "archived": 1}
    rows = await programs(app)
    assert rows["old-slug"]["id"] == known["id"] and rows["old-slug"]["title"] == "Корпоративные споры"
    assert rows["old-slug"]["source_url"] == SITE + "/programs/programma-101.html"
    assert rows["old-slug"]["cover"] and rows["old-slug"]["cover_source"] == SITE + "/media/catalog/101.jpg"
    assert rows["mediaciya"]["catalog"] == "dpo" and rows["mediaciya"]["status"] == "published"
    assert (rows["manual"]["status"], rows["club"]["status"]) == ("archived", "published")
    run = await sync_run(app)
    assert (run["found"], run["created"], run["failures"], run["error"]) == (3, 2, 0, None)

    first_cover = rows["old-slug"]["cover"]
    calls.clear()
    feed["cards"] = [
        card("101", "Корпоративные споры", thumb={"src": "/media/catalog/101-new.jpg"}),
        card("102", "Медиация"),
        card("105", "Медиация"),
    ]
    assert await center.sync_center(app.state) == {"found": 3, "created": 1, "updated": 2, "archived": 1}
    rows = await programs(app)
    assert [url for url in calls if url.endswith(".jpg")] == [
        SITE + "/media/catalog/101-new.jpg",
        SITE + "/media/catalog/105.jpg",
    ]
    assert rows["old-slug"]["cover"] != first_cover
    assert await app.state.database.rows("SELECT id FROM directus_files WHERE id=%s", (first_cover,)) == []
    assert rows["bankrotstvo"]["status"] == "archived" and rows["mediaciya-105"]["status"] == "published"


async def test_duplicate_rows_are_archived_and_removed_cover_is_cleared(dpo_center):
    app, feed, _ = dpo_center
    feed["cards"] = [card("101", "Корпоративные споры"), card("102", "Медиация"), card("104", "Банкротство")]
    await center.sync_center(app.state)
    await app.state.store.create(
        "programs", {"slug": "twin", "title": "Двойник", "hse_id": "102", "status": "published"}
    )
    old_cover = (await programs(app))["korporativnye-spory"]["cover"]
    feed["cards"][0] = card("101", "Корпоративные споры", thumb=None)
    assert (await center.sync_center(app.state))["archived"] == 1
    rows = await programs(app)
    assert sorted(row["status"] for row in rows.values() if row["hse_id"] == "102") == ["archived", "published"]
    assert (rows["korporativnye-spory"]["cover"], rows["korporativnye-spory"]["cover_source"]) == (None, None)
    assert await app.state.database.rows("SELECT id FROM directus_files WHERE id=%s", (old_cover,)) == []


async def test_incomplete_catalog_keeps_programs_and_alerts_once_after_three_failures(dpo_center):
    app, feed, _ = dpo_center
    app.state.notifications.office_text = AsyncMock()
    for number in range(8):
        await app.state.store.create(
            "programs", {"slug": f"p{number}", "title": "Программа", "hse_id": str(number), "status": "published"}
        )
    before = await programs(app)
    feed["cards"] = [card(str(number), "Программа") for number in range(3)]
    for _ in range(center.FAILURE_ALERT + 1):
        with pytest.raises(ApiError) as failure:
            await center.sync_center(app.state)
        assert failure.value.message == center.INCOMPLETE
    assert await programs(app) == before
    app.state.notifications.office_text.assert_awaited_once()
    text = app.state.notifications.office_text.await_args.args[0]
    assert text.startswith("Каталог ДПО не обновляется 3 проверки подряд: Сайт ДПО вернул неполный каталог.")
    assert (await sync_run(app))["failures"] == 4
    feed["cards"] = [card(str(number), "Программа") for number in range(4)]
    await center.sync_center(app.state)
    run = await sync_run(app)
    assert (run["failures"], run["error"], run["archived"]) == (0, None, 4)


async def test_unreachable_center_is_reported_without_raising_from_the_job(dpo_center):
    app, feed, _ = dpo_center
    feed.clear()
    assert await center.scheduled_sync(app.state) == {"error": center.NO_LIST}
    assert (await sync_run(app))["error"] == center.NO_LIST


@pytest.mark.parametrize(
    "enabled,address,minute,expected",
    [
        ("true", SITE, center.SYNC_MINUTE, True),
        ("false", SITE, center.SYNC_MINUTE, False),
        ("true", "", center.SYNC_MINUTE, False),
        ("true", SITE, center.SYNC_MINUTE + 1, False),
    ],
)
def test_center_sync_runs_hourly_only_with_address(enabled, address, minute, expected):
    state = state_with(DPO_SYNC_ENABLED=enabled, DPO_SITE_URL=address)
    assert center.due(state, datetime(2026, 10, 29, 15, minute)) is expected


async def test_office_starts_center_sync_by_hand(dpo_center, monkeypatch):
    app, feed, _ = dpo_center
    _, headers = await account(app, "admin")
    feed["cards"] = [card(str(number), "Программа") for number in range(3)]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        done = await client.post("/admin/dpo-sync", headers=headers)
        monkeypatch.setattr(app.state.settings, "DPO_SITE_URL", "")
        missing = await client.post("/admin/dpo-sync", headers=headers)
        anonymous = await client.post("/admin/dpo-sync")
    assert done.status_code == 200 and done.json()["created"] == 3
    assert missing.status_code == 409 and missing.json()["error"] == center.NO_ADDRESS
    assert anonymous.status_code in (401, 403)
