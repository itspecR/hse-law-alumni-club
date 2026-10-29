import httpx
import pytest
from pydantic import ValidationError
from test_community_integration import account

from club_api.modules.content.admin import NewsBody

NEWS = {"title": "Встреча выпускников", "body": "Текст новости"}


def test_news_time_without_zone_is_moscow_and_empty_time_is_skipped():
    assert NewsBody.model_validate({**NEWS, "published_at": "2026-10-29T15:30"}).published_at == (
        "2026-10-29T15:30:00+03:00"
    )
    assert NewsBody.model_validate({**NEWS, "published_at": ""}).published_at is None


def test_news_defaults_to_published_club_news():
    body = NewsBody.model_validate(NEWS)
    assert (body.origin, body.status, body.cover, body.source_url) == ("club", "published", None, None)


@pytest.mark.parametrize(
    "change",
    [
        {"source_url": "javascript:alert(1)"},
        {"source_url": "https://user:secret@pravo.hse.ru/news/1.html"},
        {"origin": "telegram"},
        {"status": "deleted"},
        {"title": "Н" * 256},
        {"published_at": "вчера"},
    ],
)
def test_invalid_news_fields_are_rejected(change):
    with pytest.raises(ValidationError):
        NewsBody.model_validate({**NEWS, **change})


async def test_staff_set_date_cover_origin_and_take_news_off_the_site(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        created = await client.post("/admin/news", headers=headers, json=NEWS)
        assert created.status_code == 200
        news_id = created.json()["id"]
        saved = await app.state.store.one("news", news_id)
        assert saved["published_at"] and saved["origin"] == "club"
        change = {
            "published_at": "2026-03-02T12:00",
            "cover": "https://pravo.hse.ru/data/cover.jpg",
            "origin": "faculty",
            "source_url": "https://pravo.hse.ru/news/1133936920.html",
        }
        assert (await client.patch("/admin/news/" + news_id, headers=headers, json=change)).status_code == 200
        assert (
            await client.patch("/admin/news/" + news_id, headers=headers, json={"published_at": ""})
        ).status_code == 200
        public = (await client.get("/news")).json()
        item = next(row for row in public if row["id"] == news_id)
        assert item["published_at"].startswith("2026-03-02T09:00")
        assert (item["cover"], item["origin"], item["source_url"]) == (
            change["cover"],
            "faculty",
            change["source_url"],
        )
        hidden = await client.patch("/admin/news/" + news_id, headers=headers, json={"status": "archived"})
        assert hidden.status_code == 200
        assert all(row["id"] != news_id for row in (await client.get("/news")).json())
        assert (await client.get("/news/" + item["slug"])).status_code == 404
        assert (await client.delete("/admin/news/" + news_id, headers=headers)).status_code == 200
        assert await app.state.store.read("news", filters={"id": {"_eq": news_id}}) == []
