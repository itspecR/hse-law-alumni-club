import json
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
DPO_QUESTIONS = (
    "Какой документ выдают после программы ДПО?",
    "Чем отличаются ПК и ПП?",
    "В каком формате проходят программы ДПО?",
    "Где найти программы ДПО?",
)
OPEN_QUESTIONS = 8
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


async def test_page_fragments_are_kept_out_of_search():
    async with site() as client:
        fragment = await client.get("/views/news")
        page = await client.get("/news")
    assert fragment.status_code == 200 and fragment.headers["x-robots-tag"] == "noindex"
    assert "x-robots-tag" not in page.headers


async def test_llms_file_describes_the_club_and_its_sections():
    async with site() as client:
        response = await client.get("/llms.txt")
    assert response.status_code == 200 and response.headers["content-type"] == "text/plain; charset=utf-8"
    text = response.text
    assert text.startswith("# " + SITE_NAME + "\n") and PAGES["/"][1] in text
    for path in ("/join", "/portfolio", "/events", "/news", "/podcasts", "/merch", "/give", "/support", "/documents"):
        assert "](https://pravohse.ru" + path + ")" in text
    assert "ДПО" not in text


async def test_event_with_wrong_address_is_missing_not_broken():
    async def upstream(request):
        if request.url.path == "/events" and request.url.params.get("id"):
            return httpx.Response(400, json={"error": "Некорректный идентификатор."})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(api)), base_url=SITE) as client:
            response = await client.get("/events/no-such")
    assert response.status_code == 404 and "Страница не найдена" in response.text


async def test_event_page_reports_a_broken_server_as_unavailable():
    async def upstream(request):
        return httpx.Response(503, json={"error": "Сервис временно недоступен"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(api)), base_url=SITE) as client:
            response = await client.get("/events/event-1")
    assert response.status_code == 503 and "Не удалось загрузить страницу" in response.text


async def test_home_heading_names_the_club_and_skips_hidden_dpo():
    async with site() as client:
        page = (await client.get("/")).text
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)[1]
    assert '<span class="lux-hero__name">Клуб выпускников факультета права НИУ&nbsp;ВШЭ</span>' in heading
    assert "Свои" in heading and page.count("<h1") == 1
    assert '<p class="lux-hero__next">Тренинги, события и&nbsp;подкасты.</p>' in page


async def test_home_mentions_dpo_when_the_section_is_open(dpo):
    async with site(**{"/programs": []}) as client:
        page = (await client.get("/")).text
    assert "Тренинги, ДПО по&nbsp;цене выпускника, события и&nbsp;подкасты.</p>" in page


async def test_support_hides_answers_about_closed_sections():
    async with site() as client:
        page = (await client.get("/support")).text
    for question in DPO_QUESTIONS:
        assert question not in page
    assert 'href="/dpo"' not in page and page.count('"@type": "Question"') == OPEN_QUESTIONS
    assert '<a href="/join">Открыть раздел →</a>' in page


async def test_support_shows_dpo_answers_when_the_section_is_open(dpo):
    async with site() as client:
        page = (await client.get("/support")).text
    for question in DPO_QUESTIONS:
        assert "<summary>" + question + "</summary>" in page
    assert page.count('<a href="/dpo">Открыть раздел →</a>') == len(DPO_QUESTIONS)


async def test_news_dates_are_machine_readable():
    async with site() as client:
        article = (await client.get("/news/news-one")).text
        listing = (await client.get("/news")).text
    assert '<time datetime="2026-10-01T09:00:00Z">1 октября 2026</time>' in article
    assert '<time datetime="2026-10-01T09:00:00Z">1 октября 2026</time>' in listing


async def test_app_manifest_and_chrome_do_not_promise_hidden_sections():
    async with site() as client:
        manifest = (await client.get("/manifest.webmanifest")).json()
        page = (await client.get("/give")).text
    assert "ДПО" not in manifest["description"]
    assert [item["url"] for item in manifest["shortcuts"]] == [
        "./lk?source=pwa",
        "./portfolio?source=pwa",
        "./podcasts?source=pwa",
        "./merch?source=pwa",
    ]
    assert 'aria-label="Версия для слабовидящих"><span aria-hidden="true">Аа</span></button>' in page
    assert 'id="give-transfer-save" data-transfer-save download>' in page


async def test_phones_get_a_smaller_themis_picture():
    async with site() as client:
        page = (await client.get("/")).text
        small = await client.get("/assets/themis-alive-480.webp")
    source = re.search(
        r'<source media="\(max-width: 600px\)" srcset="(/assets/themis-alive-480\.webp\?v=[0-9a-f]+)"', page
    )
    assert source and '<img class="lux-themis__poster" src="/assets/themis-alive.webp?v=' in page
    assert page.index("<picture>") < page.index(source[1]) < page.index('class="lux-themis__poster"')
    assert small.status_code == 200 and small.headers["content-type"] == "image/webp"
    assert len(small.content) < 100_000


@pytest.mark.parametrize("path", ["/favicon.ico", "/icon-192.png", "/og-card.png"])
async def test_site_icons_are_cached_for_a_day(path):
    async with site() as client:
        response = await client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "public, max-age=86400"


async def test_pages_and_manifest_are_not_cached():
    async with site() as client:
        page = await client.get("/give")
        manifest = await client.get("/manifest.webmanifest")
    assert page.headers["cache-control"] == "no-store" and manifest.headers["cache-control"] == "no-store"


async def test_cookie_banner_role_fits_its_element():
    async with site() as client:
        page = (await client.get("/give")).text
    assert '<div class="club-cookie-banner" id="cookie-banner" role="dialog"' in page


NAMED_EVENT = {**DETAILED_EVENT, "slug": "vstrecha-kluba"}
NAMED_EPISODE = {**EPISODE, "slug": "pravovaya-gramotnost"}
NAMED = {"/events": [NAMED_EVENT], "/podcasts": {"items": [NAMED_EPISODE], "subscribed": False}}


async def test_lists_link_to_readable_addresses():
    async with site(**NAMED) as client:
        events = (await client.get("/events")).text
        podcasts = (await client.get("/podcasts")).text
        home = (await client.get("/")).text
    assert '<a href="/events/vstrecha-kluba">' in events and 'href="/events/event-1"' not in events
    assert '<a href="/podcasts/pravovaya-gramotnost">' in podcasts and 'href="/podcasts/episode-1"' not in podcasts
    assert 'href="/podcasts/pravovaya-gramotnost"' in home


@pytest.mark.parametrize(
    "old,new",
    [("/events/event-1", "/events/vstrecha-kluba"), ("/podcasts/episode-1", "/podcasts/pravovaya-gramotnost")],
)
async def test_old_address_moves_to_the_readable_one(old, new):
    async with site(**NAMED) as client:
        moved = await client.get(old)
        page = await client.get(new)
        fragment = await client.get("/views" + old)
    assert moved.status_code == 301 and moved.headers["location"] == new
    assert page.status_code == 200 and head(page.text)["canonical"] == SITE + new
    assert fragment.status_code == 200


async def test_record_without_readable_address_keeps_its_old_one():
    async with site() as client:
        page = await client.get("/events/event-1")
    assert page.status_code == 200 and head(page.text)["canonical"] == SITE + "/events/event-1"


GIVE_LEDE = (
    "Пожертвование клубу&nbsp;- способ поблагодарить факультет и&nbsp;поучаствовать в&nbsp;его развитии. "
    "Средства идут на&nbsp;проекты, которые помогают факультету оставаться ведущим центром юридического образования."
)


async def test_giving_is_told_as_gratitude_and_growth():
    async with site(**{"/donations/config": {"enabled": True, "card": True, "transfer": False}}) as client:
        give = (await client.get("/give")).text
        home = (await client.get("/")).text
    assert GIVE_LEDE in give and GIVE_LEDE in home
    assert "<p>Научные исследования и&nbsp;развитие учебного процесса факультета.</p>" in give
    assert "<p>Встречи выпускников, конференции и&nbsp;семинары.</p>" in give
    for old in ("стажировки", "формы обучения", "идут на&nbsp;развитие факультета"):
        assert old not in give and old not in home
    assert head(give)["description"] == (
        "Способ поблагодарить факультет права НИУ ВШЭ и поучаствовать в его развитии. "
        "Пожертвования помогают ему оставаться ведущим центром юридического образования."
    )


ABOUT_TITLE = "О клубе - Клуб выпускников факультета права НИУ ВШЭ"
ABOUT_START = "<p>Клуб выпускников факультета права НИУ ВШЭ объединяет выпускников в сообщество"


async def test_about_page_shows_the_approved_text_until_the_office_changes_it():
    async with site() as client:
        response = await client.get("/about")
        home = (await client.get("/")).text
        guide = (await client.get("/llms.txt")).text
    page, meta = response.text, head(response.text)
    assert response.status_code == 200 and "<h1>О клубе</h1>" in page and ABOUT_START in page
    assert '<a href="mailto:club@pravohse.ru"' in page and "оставаться ведущим центром юридического образования" in page
    for removed in ("подкаст", "формы обучения", "стажировки"):
        assert removed not in page.split('<main id="main"')[1].split("</main>")[0]
    assert meta["title"] == ABOUT_TITLE and meta["canonical"] == SITE + "/about" and meta["robots"] == "index,follow"
    assert meta["description"].startswith("Клуб объединяет выпускников факультета права НИУ ВШЭ.")
    assert '"@type": "AboutPage"' in page and '<a href="/about">О клубе</a>' in home
    assert "](https://pravohse.ru/about)" in guide


@pytest.mark.parametrize("reply", [[], None, "текст"])
async def test_about_page_survives_an_unexpected_reply(reply):
    async with site(**{"/pages/about": reply}) as client:
        response = await client.get("/about")
    assert response.status_code == 200 and ABOUT_START in response.text


async def test_about_page_takes_texts_from_the_office():
    saved = {
        "title": "Наш клуб",
        "body": "Первый абзац.\n\n## Раздел\n\nВторой <b>абзац</b>.",
        "meta_title": "Клуб в поиске",
        "meta_description": "",
    }
    async with site(**{"/pages/about": saved}) as client:
        page = (await client.get("/about")).text
    meta = head(page)
    assert "<h1>Наш клуб</h1>" in page and "<p>Первый абзац.</p>" in page and "<h2>Раздел</h2>" in page
    assert "<b>абзац</b>" not in page and ABOUT_START not in page
    assert meta["title"] == "Клуб в поиске"
    assert meta["description"].startswith("Клуб объединяет выпускников факультета права НИУ ВШЭ.")


async def test_section_of_club_programs_is_called_trainings():
    async with site() as client:
        listing = await client.get("/portfolio")
        home = (await client.get("/")).text
        program = (await client.get("/portfolio/club-one")).text
    page, meta = listing.text, head(listing.text)
    assert "<h1>Тренинги по развитию навыков</h1>" in page and '<a href="/portfolio"' in home
    assert meta["title"] == "Тренинги по развитию навыков" + " - " + SITE_NAME
    assert meta["description"].startswith("Тренинги по развитию навыков от клуба выпускников факультета права НИУ ВШЭ.")
    crumbs = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', program, re.S)[1])
    names = [item["name"] for node in crumbs["@graph"] for item in node.get("itemListElement", [])]
    assert "</span> Тренинги</a>" in program and names[:2] == ["Главная", "Тренинги"]
    for old in ("Портфель программ", "Программы клуба"):
        assert old not in page and old not in home and old not in program


async def test_home_tells_about_yearly_meetings():
    async with site() as client:
        home = (await client.get("/")).text
    block = home.split('id="lux-meet-title"')[1].split("</section>")[0]
    first = '<p class="lux-lede">Каждый год клуб собирает выпускников разных лет на&nbsp;традиционную встречу.</p>'
    assert first in block and block.index(first) < block.index("27&nbsp;февраля 2026&nbsp;года")
