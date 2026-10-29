import io

import httpx
import pytest
from PIL import Image

from club_api.modules.news.article import parse_article
from club_api.modules.news.importer import sync_news
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


def article(title):
    return (
        '<div class="articleMeta"><div class="articleMetaItem"><div class="articleMetaItem__label '
        'articleMetaItem__label--date">Дата</div><div class="articleMetaItem__content">13 февраля, 2024 г.</div>'
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
        body = pages.get(url) or article(url.rsplit("/", 1)[-1])
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
