import re

import httpx
import pytest

from club_web.main import create_app
from club_web.office import filter_content
from club_web.rich_text import rich_text

MEDIA_ID = "3f2a1b4c-1111-2222-3333-444455556666"
FACULTY = {
    "id": "news-2",
    "slug": "faculty-one",
    "title": "Встреча выпускников факультета",
    "excerpt": "Лид новости",
    "body": "## Итоги\n\nПервый абзац с **жирным**.\n\n![Зал](" + MEDIA_ID + ")",
    "published_at": "2026-03-02T09:00:00Z",
    "cover": MEDIA_ID,
    "origin": "faculty",
    "source_url": "https://pravo.hse.ru/news/1133936920.html",
    "status": "published",
}
CLUB = {
    "id": "news-1",
    "slug": "club-one",
    "title": "Черновик клуба",
    "body": "Текст",
    "published_at": "2026-10-01T09:00:00Z",
    "origin": "club",
    "status": "draft",
}
ENDPOINTS = {
    "/news": [FACULTY],
    "/news/faculty-one": FACULTY,
    "/auth/admin-session": {"role": "admin"},
    "/admin/news": [FACULTY, CLUB],
}
ADMIN = {"authorization": "Bearer admin-test"}


@pytest.fixture
async def client():
    async def upstream(request):
        path = request.url.path
        if path in ENDPOINTS:
            return httpx.Response(200, json=ENDPOINTS[path])
        return httpx.Response(200, json=[]) if path.startswith("/admin/") else httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        app = create_app(api)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://web.test") as web:
            yield web


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Один\n\nДва\nстрока", "<p>Один</p>\n<p>Два<br>\nстрока</p>"),
        ("# Крупный\n\n###### Мелкий", "<h2>Крупный</h2>\n<h4>Мелкий</h4>"),
        ("**жирный** и *курсив*", "<p><strong>жирный</strong> и <em>курсив</em></p>"),
        ("- раз\n- два", "<ul>\n<li>раз</li>\n<li>два</li>\n</ul>"),
        ("[сайт](https://pravo.hse.ru)", '<a href="https://pravo.hse.ru" rel="noopener noreferrer">сайт</a>'),
        ("<script>alert(1)</script>", "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"),
        ("[ссылка](javascript:alert(1))", "<p>[ссылка](javascript:alert(1))</p>"),
        ("![фото](" + MEDIA_ID + ")", '<img src="/api/media/' + MEDIA_ID + '" alt="фото">'),
        ("`код`", "<p>`код`</p>"),
    ],
)
def test_rich_text_renders_safe_formatting(source, expected):
    assert expected in str(rich_text(source))


def test_rich_text_drops_foreign_images_and_empty_text():
    assert "<img" not in str(rich_text("![чужое](https://example.test/a.png)"))
    assert str(rich_text(None)) == ""


async def test_faculty_news_page_shows_cover_formatted_text_and_source(client):
    page = (await client.get("/news/faculty-one")).text
    assert '<img class="news-cover" src="/api/media/' + MEDIA_ID + '"' in page
    assert "<h2>Итоги</h2>" in page and "<strong>жирным</strong>" in page
    assert '<img src="/api/media/' + MEDIA_ID + '" alt="Зал">' in page
    assert "Источник: факультет права НИУ ВШЭ." in page
    assert 'href="https://pravo.hse.ru/news/1133936920.html"' in page and "Оригинал на сайте факультета" in page


async def test_news_list_marks_faculty_news_and_shows_cover(client):
    page = (await client.get("/news")).text
    assert '<article class="has-cover">' in page
    assert "</time> · Факультет права</p>" in page
    assert '<img class="news-thumb" src="/api/media/' + MEDIA_ID + '"' in page


async def test_office_news_form_has_all_fields_and_formatting_hint(client):
    page = (await client.get("/views/admin/news", headers=ADMIN)).text
    create = page.split("</details>")[0]
    names = re.findall(r'name="(\w+)"', create)
    assert names == ["title", "published_at", "excerpt", "body", "cover", "origin", "source_url", "status"]
    assert "## Подзаголовок" in create and "ID файла из «Медиа»" in create
    assert '<option value="archived" >Снята с публикации</option>' in create
    assert '<option value="faculty" >Факультет права</option>' in create
    assert '<select name="origin" aria-label="Откуда">' in page
    assert page.count("Открыть на сайте") == 1 and 'href="/news/faculty-one"' in page


def test_office_news_filter_by_origin_and_status():
    rows = [FACULTY, CLUB]
    assert filter_content(rows, {"origin": "faculty"}) == [FACULTY]
    assert filter_content(rows, {"origin": "club", "status": "draft"}) == [CLUB]
    assert filter_content(rows, {"origin": "club", "status": "published"}) == []
