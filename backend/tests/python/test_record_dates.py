from datetime import UTC, datetime

from test_community_integration import account
from test_portfolio_applications import client_for

from club_api.core.models import touched
from club_api.modules.content.sitemap import record_urls, records

EMPTY = {"news": [], "portfolio": [], "events": [], "podcasts": [], "products": [], "campaigns": []}
EVENT = {"title": "Встреча с датой", "starts_at": "2099-05-20T19:00"}
OLD_MOMENT = datetime(2020, 2, 3, 10, tzinfo=UTC)


def dates(found):
    return {path: modified for path, _, _, modified in record_urls({**EMPTY, **found})}


def test_saving_a_record_marks_the_moment():
    before = datetime.now(UTC)
    saved = touched({"title": "Запись"})
    assert saved["title"] == "Запись" and before <= saved["updated_at"] <= datetime.now(UTC)


def test_sitemap_dates_every_record_that_knows_its_change():
    found = {
        "news": [
            {"slug": "edited", "published_at": "2026-11-10T09:00:00Z", "updated_at": "2026-11-12T08:00:00Z"},
            {"slug": "fresh", "published_at": "2026-11-10T09:00:00Z", "updated_at": None},
        ],
        "portfolio": [{"slug": "talks", "updated_at": "2026-10-01T00:00:00Z"}],
        "events": [{"id": "e-1", "slug": "vstrecha", "updated_at": "2026-09-05T10:00:00Z"}],
        "podcasts": [{"id": "p-1", "slug": None, "updated_at": None}],
        "products": [{"slug": "bag", "updated_at": "2026-08-07T10:00:00Z"}],
        "campaigns": [{"slug": "intensive", "updated_at": "2026-07-09T10:00:00Z"}],
    }
    assert dates(found) == {
        "/news/edited": "2026-11-12",
        "/news/fresh": "2026-11-10",
        "/portfolio/talks": "2026-10-01",
        "/events/vstrecha": "2026-09-05",
        "/podcasts/p-1": "",
        "/merch/bag": "2026-08-07",
        "/give/intensive": "2026-07-09",
    }


async def stored_dates(app):
    return {path: modified for path, _, _, modified in record_urls(await records(app.state.store))}


async def test_office_edit_moves_the_date_in_the_sitemap(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    today = datetime.now(UTC).date().isoformat()
    path = "/events/vstrecha-s-datoj"
    async with client_for(app) as client:
        created = await client.post("/admin/events", headers=headers, json={**EVENT, "slug": "vstrecha-s-datoj"})
        event = created.json()["id"]
        assert (await stored_dates(app))[path] == today
        await app.state.store.update("events", {"updated_at": OLD_MOMENT}, id=event)
        assert (await stored_dates(app))[path] == OLD_MOMENT.date().isoformat()
        await client.patch("/admin/events/" + event, headers=headers, json={"location": "Москва"})
        assert (await stored_dates(app))[path] == today
        news = await client.post("/admin/news", headers=headers, json={"title": "Новость с датой", "body": "Текст"})
        sitemap = (await client.get("/sitemap.xml")).text
    assert news.status_code == 200 and (await stored_dates(app))["/news/novost-s-datoj"] == today
    assert "/events/vstrecha-s-datoj</loc><lastmod>" + today + "</lastmod>" in sitemap
