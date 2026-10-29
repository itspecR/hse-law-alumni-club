import io
from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from PIL import Image
from test_community_integration import account

from club_api.core.models import CLUB_TIMEZONE
from club_api.modules.news import schedule
from club_api.modules.news.article import parse_article
from club_api.modules.news.importer import FAILURE_ALERT, sync_news
from club_api.modules.news.sources import SOURCES, canonical_news_url, parse_source

ALUMNI, TAG = (source["url"] for source in SOURCES)
FIRST, SECOND, THIRD = (f"https://pravo.hse.ru/news/{number}.html" for number in (101, 102, 103))
SUBSITE = "https://pravo.hse.ru/intlaw/news/104.html"


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), "orange").save(buffer, "JPEG")
    return buffer.getvalue()


def alumni_page(*urls):
    cards = "".join(
        f'<div class="masonry__item"><div class="plate_news__title h4"><a href="{url}">Новость {index}</a></div></div>'
        for index, url in enumerate(urls, 1)
    )
    return f'<div class="plate_news masonry">{cards}</div>'


def tag_page(*urls):
    posts = "".join(
        f'<div class="post"><div class="post__content"><h2><a href="{url}?utm=1" title="Тема {index}">Тема</a></h2></div></div>'
        for index, url in enumerate(urls, 1)
    )
    return f'<div class="posts posts_general">{posts}</div>'


def article(title, day=13):
    return (
        '<div class="articleMeta"><div class="articleMetaItem"><div class="articleMetaItem__label '
        'articleMetaItem__label--date">Дата</div><div class="articleMetaItem__content">'
        + str(day)
        + " февраля, 2024 г.</div>"
        '</div></div><div class="post__text"><div class="news_item annotation"><div class="lead-in">Лид '
        + title
        + '</div></div><div class="picture head-picture"><p><img src="/data/cover.jpg"></p>'
        '<div class="small">© Факультет права НИУ ВШЭ</div></div>'
        "<p><strong>1</strong><strong> место</strong> — команда *звезда*</p><script>alert(1)</script>"
        '<ul><li>Первый</li><li><a href="/news/1.html">Второй</a></li></ul>'
        '<p><img src="/data/inline.jpg" alt="Зал"></p><p>Итог</p></div>'
    )


def test_news_urls_keep_faculty_articles_only():
    assert canonical_news_url(FIRST + "?utm_source=tg#top") == FIRST
    assert canonical_news_url(SUBSITE) == SUBSITE
    for url in ("http://pravo.hse.ru/news/1.html", "https://hse.ru/news/1.html", "https://pravo.hse.ru/news/tags/"):
        assert canonical_news_url(url) is None


def test_both_source_pages_are_parsed_with_titles():
    assert parse_source(alumni_page(FIRST, "/news/102.html", FIRST), ALUMNI) == [
        {"source_url": FIRST, "title": "Новость 1"},
        {"source_url": SECOND, "title": "Новость 2"},
    ]
    assert parse_source(tag_page(THIRD, SUBSITE), TAG) == [
        {"source_url": THIRD, "title": "Тема 1"},
        {"source_url": SUBSITE, "title": "Тема 2"},
    ]


def test_article_becomes_lead_cover_and_safe_markdown():
    result = parse_article(article("первой"), FIRST)
    assert result["published_at"].startswith("2024-02-13")
    assert (result["excerpt"], result["cover"]) == ("Лид первой", "https://pravo.hse.ru/data/cover.jpg")
    assert result["blocks"] == [
        "**1** **место** — команда \\*звезда\\*",
        "- Первый\n- [Второй](https://pravo.hse.ru/news/1.html)",
        ("image", "https://pravo.hse.ru/data/inline.jpg", "Зал"),
        "Итог",
    ]
    assert parse_article("<p>Нет текста</p>", FIRST) is None


@pytest.fixture
def faculty(database_app):
    calls, broken = [], {SECOND}
    pages = {ALUMNI: alumni_page(FIRST, SECOND), TAG: tag_page(FIRST, SUBSITE)}

    async def handler(request):
        url = str(request.url)
        calls.append(url)
        if url.endswith(".jpg"):
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        if url in broken:
            return httpx.Response(500, text="", headers={"content-type": "text/html"})
        name = url.rsplit("/", 1)[-1]
        body = pages.get(url) or article(name, int(name.removesuffix(".html")) - 100)
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    database_app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return database_app, calls, broken


async def faculty_news(app):
    rows = await app.state.store.read(
        "news", fields=("id", "source_url", "status", "origin", "cover", "body", "excerpt")
    )
    return {row["source_url"]: row for row in rows}


async def inbox(app):
    return {row["source_url"]: row for row in await app.state.database.rows("SELECT * FROM club_news_inbox")}


async def test_sync_publishes_new_faculty_news_once_with_images(faculty):
    app, calls, broken = faculty
    result = await sync_news(app.state, delay=0)
    assert [(row["id"], row["found"], row["added"]) for row in result["sources"]] == [
        ("alumni", 2, 1),
        ("alumni-tag", 2, 1),
    ]
    news = await faculty_news(app)
    assert set(news) == {FIRST, SUBSITE}
    first = news[FIRST]
    assert (first["status"], first["origin"], first["excerpt"]) == ("published", "faculty", "Лид 101.html")
    assert first["cover"] and "![Зал](" in first["body"] and first["cover"] not in first["body"]
    assert (await inbox(app))[SECOND]["state"] == "new"
    broken.clear()
    calls.clear()
    second = await sync_news(app.state, delay=0)
    assert [row["added"] for row in second["sources"]] == [1, 0]
    assert FIRST not in calls and SECOND in calls and set(await faculty_news(app)) == {FIRST, SECOND, SUBSITE}
    _, headers = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        panel = (await client.get("/admin/news-sources", headers=headers)).json()
    assert [item["source_url"] for item in panel["items"]] == [SUBSITE, SECOND, FIRST]
    assert [item["news_status"] for item in panel["items"]] == ["published"] * 3


async def test_source_failure_reason_is_saved_for_the_panel(faculty):
    app, _, broken = faculty
    broken.add(TAG)
    result = await sync_news(app.state, delay=0)
    assert result["sources"][1] == {"id": "alumni-tag", "error": "Источник временно недоступен."}
    runs = {row["source"]: row for row in await app.state.database.rows("SELECT * FROM club_news_source_runs")}
    assert runs["alumni-tag"]["error"] == "Источник временно недоступен." and runs["alumni"]["error"] is None


async def test_deleted_or_dismissed_news_never_return(faculty):
    app, _, broken = faculty
    broken.clear()
    await sync_news(app.state, delay=0)
    news = await faculty_news(app)
    await app.state.store.delete("news", id=news[FIRST]["id"])
    await app.state.database.execute("UPDATE club_news_inbox SET state='dismissed' WHERE source_url=%s", (SUBSITE,))
    await app.state.store.delete("news", id=news[SUBSITE]["id"])
    result = await sync_news(app.state, delay=0)
    assert [row["added"] for row in result["sources"]] == [0, 0]
    assert set(await faculty_news(app)) == {SECOND}
    assert (await inbox(app))[FIRST]["state"] == "imported"
    assert str((await inbox(app))[FIRST]["published_at"]).startswith("2024-02-01")


def moscow(day, hour, minute):
    return datetime(2026, 10, day, hour, minute, tzinfo=CLUB_TIMEZONE)


@pytest.mark.parametrize(
    "moment,interval,expected",
    [
        (moscow(29, 14, 10), 3, moscow(29, 15, 17)),
        (moscow(29, 15, 20), 3, moscow(29, 18, 17)),
        (moscow(29, 14, 17), 1, moscow(29, 15, 17)),
        (moscow(29, 1, 0), 24, moscow(30, 0, 17)),
        (moscow(29, 23, 30), 6, moscow(30, 0, 17)),
    ],
)
def test_next_check_is_at_minute_17_of_hours_divisible_by_interval(moment, interval, expected):
    assert schedule.next_check(moment, interval) == expected


async def test_scheduled_check_follows_panel_settings(database_app, monkeypatch):
    calls = []
    monkeypatch.setattr(schedule, "sync_news", AsyncMock(side_effect=lambda state: calls.append(1)))
    assert await schedule.news_settings(database_app.state) == {"enabled": False, "interval_hours": 3}
    await schedule.scheduled_news(database_app.state, moscow(29, 15, 17))
    await schedule.save_settings(database_app.state, True, 3)
    await schedule.scheduled_news(database_app.state, moscow(29, 16, 17))
    await schedule.scheduled_news(database_app.state, moscow(29, 15, 17))
    await schedule.save_settings(database_app.state, True, 24)
    await schedule.scheduled_news(database_app.state, moscow(29, 15, 17))
    await schedule.scheduled_news(database_app.state, moscow(30, 0, 17))
    assert len(calls) == 2


async def test_panel_saves_schedule_and_rejects_unknown_interval(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        saved = await client.patch(
            "/admin/news-sources/settings", headers=headers, json={"enabled": True, "interval_hours": "6"}
        )
        assert saved.status_code == 200
        plan = (await client.get("/admin/news-sources", headers=headers)).json()["schedule"]
        wrong = await client.patch(
            "/admin/news-sources/settings", headers=headers, json={"enabled": True, "interval_hours": "5"}
        )
        anonymous = await client.patch("/admin/news-sources/settings", json={"enabled": False, "interval_hours": "3"})
    assert (plan["enabled"], plan["interval_hours"], plan["intervals"]) == (True, 6, [1, 3, 6, 12, 24])
    assert plan["allowed"] is False and plan["next_check"] is None
    assert wrong.status_code == 400 and anonymous.status_code in (401, 403)
    assert await schedule.news_settings(app.state) == {"enabled": True, "interval_hours": 6}


async def test_staff_are_alerted_once_after_three_failures_in_a_row(faculty):
    app, _, broken = faculty
    app.state.notifications.office_text = AsyncMock()
    broken.add(TAG)
    for _ in range(FAILURE_ALERT + 1):
        await sync_news(app.state, delay=0)
    app.state.notifications.office_text.assert_awaited_once()
    text = app.state.notifications.office_text.await_args.args[0]
    assert text.startswith("Источник новостей «Новости факультета с темой «выпускники»» не отвечает 3 проверки подряд")
    assert text.endswith("раздел «Источники новостей».")
    broken.discard(TAG)
    await sync_news(app.state, delay=0)
    runs = {row["source"]: row for row in await app.state.database.rows("SELECT * FROM club_news_source_runs")}
    assert (runs["alumni-tag"]["failures"], runs["alumni-tag"]["error"]) == (0, None)
