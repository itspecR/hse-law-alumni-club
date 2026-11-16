import importlib
import re
from uuid import uuid4

import pytest
from test_community_integration import account
from test_portfolio_applications import client_for

from club_api.core.errors import ApiError
from club_api.core.models import record_key
from club_api.modules.content.sitemap import record_urls

EVENT = {"title": "Встреча выпускников", "starts_at": "2099-05-20T19:00"}
EPISODE = {"title": "Выпуск о договорах"}
EMPTY = {"news": [], "portfolio": [], "events": [], "podcasts": [], "products": [], "campaigns": []}
slugs = importlib.import_module("club_api.db.migrations.0030_event_podcast_slugs")


def test_record_is_found_by_identifier_or_by_address():
    identifier = str(uuid4())
    assert record_key(identifier) == {"id": {"_eq": identifier}}
    assert record_key("vstrecha-vypusknikov") == {"slug": {"_eq": "vstrecha-vypusknikov"}}


@pytest.mark.parametrize("value", ["Не адрес", "UPPER", "a" * 256, "-edge-", "", None])
def test_text_that_is_neither_identifier_nor_address_is_rejected(value):
    with pytest.raises(ApiError):
        record_key(value)


def test_sitemap_prefers_the_readable_address():
    found = {**EMPTY, "events": [{"id": "e-1", "slug": "vstrecha"}], "podcasts": [{"id": "p-1", "slug": None}]}
    assert [path for path, *_ in record_urls(found)] == ["/events/vstrecha", "/podcasts/p-1"]


def test_old_records_get_an_address_from_the_title():
    identifier = uuid4()
    assert slugs.free_slug("Встреча выпускников", set(), identifier) == "vstrecha-vypusknikov"
    assert slugs.free_slug("Встреча выпускников", {"vstrecha-vypusknikov"}, identifier) == (
        "vstrecha-vypusknikov-" + str(identifier)[: slugs.ID_PART]
    )
    assert slugs.free_slug(None, set(), identifier) == str(identifier)


async def test_event_gets_an_address_and_opens_by_it(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    async with client_for(app) as client:
        first = (await client.post("/admin/events", headers=headers, json=EVENT)).json()["id"]
        second = (await client.post("/admin/events", headers=headers, json=EVENT)).json()["id"]
        by_address = (await client.get("/events", params={"id": "vstrecha-vypusknikov"})).json()
        by_identifier = (await client.get("/events", params={"id": first})).json()
        missing = (await client.get("/events", params={"id": "net-takogo"})).json()
        sitemap = (await client.get("/sitemap.xml")).text
    assert [row["id"] for row in by_address] == [first] == [row["id"] for row in by_identifier]
    assert by_address[0]["slug"] == "vstrecha-vypusknikov" and missing == []
    other = (await app.state.store.one("events", second))["slug"]
    assert re.fullmatch(r"vstrecha-vypusknikov-[0-9a-f]{8}", other)
    assert "/events/vstrecha-vypusknikov</loc>" in sitemap and "/events/" + other + "</loc>" in sitemap


async def test_episode_gets_an_address(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    async with client_for(app) as client:
        created = await client.post("/admin/podcasts", headers=headers, json=EPISODE)
        listed = (await client.get("/podcasts")).json()["items"]
        sitemap = (await client.get("/sitemap.xml")).text
    assert created.status_code == 200
    assert [row["slug"] for row in listed if row["id"] == created.json()["id"]] == ["vypusk-o-dogovorah"]
    assert "/podcasts/vypusk-o-dogovorah</loc>" in sitemap


async def test_staff_set_and_change_the_address_of_an_event(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    async with client_for(app) as client:
        chosen = await client.post("/admin/events", headers=headers, json={**EVENT, "slug": "majskaya-vstrecha"})
        other = await client.post("/admin/events", headers=headers, json={**EVENT, "slug": ""})
        first, second = chosen.json()["id"], other.json()["id"]
        taken = await client.patch("/admin/events/" + second, headers=headers, json={"slug": "majskaya-vstrecha"})
        same = await client.patch("/admin/events/" + first, headers=headers, json={"slug": "majskaya-vstrecha"})
        kept = await client.patch("/admin/events/" + first, headers=headers, json={"title": "Новое имя", "slug": ""})
        renamed = await client.patch("/admin/events/" + second, headers=headers, json={"slug": "letnyaya-vstrecha"})
        wrong = await client.patch("/admin/events/" + second, headers=headers, json={"slug": "Летняя встреча"})
        listed = (await client.get("/admin/events", headers=headers)).json()
    assert (taken.status_code, taken.json()["error"]) == (409, "Такой адрес уже занят.")
    assert (same.status_code, kept.status_code, renamed.status_code, wrong.status_code) == (200, 200, 200, 400)
    rows = listed["items"] if isinstance(listed, dict) else listed
    assert {row["id"]: row["slug"] for row in rows if row["id"] in (first, second)} == {
        first: "majskaya-vstrecha",
        second: "letnyaya-vstrecha",
    }


async def test_staff_set_the_address_of_an_episode(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    async with client_for(app) as client:
        created = await client.post("/admin/podcasts", headers=headers, json={**EPISODE, "slug": "dogovory"})
        episode = created.json()["id"]
        clash = await client.post("/admin/podcasts", headers=headers, json={**EPISODE, "slug": "dogovory"})
        changed = await client.patch("/admin/podcasts/" + episode, headers=headers, json={"slug": "vse-o-dogovorah"})
        listed = (await client.get("/admin/podcasts", headers=headers)).json()
    assert (created.status_code, clash.status_code, changed.status_code) == (200, 409, 200)
    assert [row["slug"] for row in listed if row["id"] == episode] == ["vse-o-dogovorah"]
