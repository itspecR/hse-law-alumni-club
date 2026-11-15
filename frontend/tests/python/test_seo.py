import re
from contextlib import asynccontextmanager

import httpx
import pytest
from test_pages import EPISODE, EVENT, NEWS, PORTFOLIO, PRODUCT, PUBLIC_CAMPAIGN

from club_web.main import create_app
from club_web.seo import DESCRIPTION_LIMIT, PAGES, SITE_NAME, brief

SITE = "http://web.test"
FACULTY_NEWS = {
    **NEWS,
    "slug": "faculty-one",
    "excerpt": "Короткий анонс новости факультета.",
    "origin": "faculty",
    "source_url": "https://pravo.hse.ru/news/1.html",
    "cover": "11111111-2222-3333-4444-555555555555",
}
DETAILED_EVENT = {**EVENT, "description": "Встреча выпускников разных лет", "location": "Москва, Покровский бульвар"}
ENDPOINTS = {
    "/pages/home": {"blocks": {}},
    "/news": [NEWS],
    "/news/news-one": {**NEWS, "excerpt": "Анонс новости клуба."},
    "/news/faculty-one": FACULTY_NEWS,
    "/portfolio": [PORTFOLIO],
    "/portfolio/club-one": {**PORTFOLIO, "description": "Практика переговоров для юристов."},
    "/products": [{**PRODUCT, "description": "Плотная сумка с эмблемой клуба."}],
    "/events": [DETAILED_EVENT],
    "/podcasts": {"items": [{**EPISODE, "description": "Разбор изменений в праве."}], "subscribed": False},
    "/donations/config": {"enabled": False, "card": False, "transfer": False},
    "/campaigns/intensive": PUBLIC_CAMPAIGN,
    "/support/config": {"enabled": True, "version": "test-version", "consent": "Тестовое согласие"},
}


@asynccontextmanager
async def site(**replaced):
    endpoints = {**ENDPOINTS, **replaced}

    async def upstream(request):
        if request.url.path in endpoints:
            return httpx.Response(200, json=endpoints[request.url.path])
        return httpx.Response(404, json={"error": "Не найдено"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(api)), base_url=SITE) as client:
            yield client


def head(page):
    found = {
        "title": re.search(r"<title>(.*?)</title>", page)[1],
        "canonical": (re.search(r'<link rel="canonical" href="([^"]*)">', page) or [None, None])[1],
    }
    found.update(re.findall(r'<meta (?:name|property)="([^"]+)" content="([^"]*)">', page))
    return found


async def head_of(path, **replaced):
    async with site(**replaced) as client:
        return head((await client.get(path)).text)


async def test_home_has_approved_title_description_and_share_preview():
    async with site() as client:
        meta = head((await client.get("/?utm_source=chat&source=pwa")).text)
        card = await client.get("/og-card.png")
    assert meta["title"] == SITE_NAME and meta["description"] == PAGES["/"][1]
    assert meta["robots"] == "index,follow" and meta["canonical"] == SITE + "/"
    assert meta["og:title"] == SITE_NAME and meta["og:description"] == PAGES["/"][1]
    assert meta["og:url"] == SITE + "/" and meta["og:type"] == "website" and meta["og:locale"] == "ru_RU"
    assert meta["og:site_name"] == SITE_NAME and meta["twitter:card"] == "summary_large_image"
    assert (meta["og:image"], meta["og:image:width"], meta["og:image:height"]) == (SITE + "/og-card.png", "1200", "630")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"


async def test_every_section_has_its_own_title_and_description():
    async with site() as client:
        metas = {path: head((await client.get(path)).text) for path in PAGES}
    for path, (title, description) in PAGES.items():
        assert (metas[path]["title"], metas[path]["description"]) == (title, description), path
        assert metas[path]["canonical"] == SITE + path and metas[path]["robots"] == "index,follow", path
    assert len({meta["title"] for meta in metas.values()}) == len(PAGES)
    assert len({meta["description"] for meta in metas.values()}) == len(PAGES)
    assert all(len(description) <= 200 for _, description in PAGES.values())


@pytest.mark.parametrize(
    ("path", "title", "description"),
    [
        ("/news/news-one", "Новость клуба - " + SITE_NAME, "Анонс новости клуба."),
        (
            "/events/event-1",
            "Встреча клуба, 1 ноября 2026 - " + SITE_NAME,
            "Встреча выпускников разных лет. 1 ноября 2026. Место: Москва, Покровский бульвар.",
        ),
        (
            "/portfolio/club-one",
            "Переговоры для юристов - программа клуба выпускников факультета права НИУ ВШЭ",
            "Практика переговоров для юристов. Старт 1 декабря 2026.",
        ),
        (
            "/podcasts/episode-1",
            "Правовая грамотность - подкаст «Правовая грамотность»",
            "Разбор изменений в праве.",
        ),
        (
            "/merch/bag-one",
            "Сумка клуба - мерч клуба выпускников факультета права НИУ ВШЭ",
            "Плотная сумка с эмблемой клуба.",
        ),
        (
            "/give/intensive",
            "Искусство влияния - " + SITE_NAME,
            "Имидж, речь, аудитория. Клуб собирает средства на оснащение аудитории.",
        ),
    ],
)
async def test_record_pages_take_title_and_description_from_the_record(path, title, description):
    meta = await head_of(path)
    assert (meta["title"], meta["description"], meta["og:title"]) == (title, description, title)
    assert meta["robots"] == "index,follow" and meta["canonical"] == SITE + path == meta["og:url"]


async def test_record_without_text_gets_a_description_from_its_title():
    meta = await head_of("/merch/bag-one", **{"/products": [PRODUCT]})
    assert meta["description"] == "Сумка клуба. " + SITE_NAME + "."


async def test_news_is_an_article_and_faculty_news_points_to_the_original():
    own = await head_of("/news/news-one")
    faculty = await head_of("/news/faculty-one")
    assert own["og:type"] == "article" and own["canonical"] == SITE + "/news/news-one"
    assert faculty["canonical"] == "https://pravo.hse.ru/news/1.html" and faculty["robots"] == "index,follow"
    assert faculty["og:url"] == SITE + "/news/faculty-one"
    assert faculty["og:image"] == SITE + "/api/media/11111111-2222-3333-4444-555555555555"
    assert "og:image:width" not in faculty


async def test_event_opened_by_parameter_points_to_its_own_address():
    meta = await head_of("/events?event=event-1")
    assert meta["canonical"] == SITE + "/events/event-1" and meta["title"].startswith("Встреча клуба, 1 ноября 2026")


@pytest.mark.parametrize(
    "path", ["/search", "/forgot", "/support/consent", "/changes", "/lk", "/cart", "/saved", "/give/thanks", "/reset"]
)
async def test_service_pages_stay_out_of_search(path):
    meta = await head_of(path)
    assert meta["robots"] == "noindex,nofollow" and meta["canonical"] is None


@pytest.mark.parametrize("path", ["/no-such-page", "/news/missing", "/portfolio/missing", "/give/missing"])
async def test_missing_pages_stay_out_of_search(path):
    async with site() as client:
        response = await client.get(path)
    meta = head(response.text)
    assert response.status_code == 404 and meta["robots"] == "noindex,nofollow" and meta["canonical"] is None
    assert meta["title"] == "Страница не найдена - " + SITE_NAME


@pytest.mark.parametrize(
    ("path", "endpoint", "empty"),
    [
        ("/portfolio", "/portfolio", []),
        ("/news", "/news", []),
        ("/merch", "/products", []),
        ("/events", "/events", []),
        ("/podcasts", "/podcasts", {"items": [], "subscribed": False}),
    ],
)
async def test_empty_list_waits_for_records_before_search_sees_it(path, endpoint, empty):
    filled = await head_of(path)
    waiting = await head_of(path, **{endpoint: empty})
    assert filled["robots"] == "index,follow"
    assert waiting["robots"] == "noindex,follow" and waiting["canonical"] == SITE + path


async def test_public_address_comes_from_settings(monkeypatch):
    monkeypatch.setenv("PUBLIC_URL", "https://club.example.org/")
    meta = await head_of("/join")
    assert meta["canonical"] == "https://club.example.org/join" == meta["og:url"]
    assert meta["og:image"] == "https://club.example.org/og-card.png" and meta["robots"] == "index,follow"


def test_description_is_cut_at_a_word():
    text = brief("слово " * 60)
    assert len(text) <= DESCRIPTION_LIMIT and text.endswith("слово") and "  " not in text
    assert brief("Короткий текст.", "", None) == "Короткий текст."
