import re
from datetime import date
from pathlib import Path

import httpx
import pytest

import club_web
from club_web import office
from club_web.build import build_public
from club_web.catalog import club_programs_preview
from club_web.formatting import local_datetime, payment_url, program_price, program_start, rub, safe_url
from club_web.main import create_app
from club_web.office import filter_content
from club_web.pages import NAV, OFFICE_NAV, PUBLIC_PAGES
from club_web.views import PUBLIC

NEWS = {
    "id": "news-1",
    "slug": "news-one",
    "title": "Новость клуба",
    "body": "<script>alert(1)</script>",
    "published_at": "2026-10-01T09:00:00Z",
}
PROGRAM = {
    "id": "program-1",
    "slug": "course-one",
    "title": "Правовая программа",
    "direction": "Право",
    "duration": "40 часов",
    "format": "online",
    "price": 300000,
    "enrollment": "actual",
    "modules": [],
    "teachers": [],
}
PORTFOLIO = {
    "id": "club-1",
    "slug": "club-one",
    "title": "Переговоры для юристов",
    "direction": "Переговоры",
    "format": "blended",
    "duration": "6 недель",
    "price": 4500000,
    "enrollment": "actual",
    "dates": {"start": "1 декабря 2026"},
    "document": "Сертификат об обучении",
    "cover": "",
    "capacity": 20,
    "seats_left": 17,
    "group": {"min": 2, "max": 20},
    "description": "Практический курс.",
    "audience": ["Юристы компаний"],
    "competencies": ["Подготовка позиции"],
    "communication": ["Telegram-чат группы"],
    "trainers": [{"id": "trainer-1", "fio": "Анна Вымышленная", "photo": "", "bio": "Медиатор"}],
}
PRODUCT = {
    "id": "product-1",
    "slug": "bag-one",
    "title": "Сумка клуба",
    "category": "Аксессуары",
    "price": 50000,
    "stock": 3,
    "images": [],
    "variants_json": [],
}
EVENT = {
    "id": "event-1",
    "title": "Встреча клуба",
    "starts_at": "2026-11-01T12:00:00Z",
    "status": "published",
    "format": "offline",
    "points": 60,
}
EPISODE = {
    "id": "episode-1",
    "title": "Правовая грамотность",
    "duration": "30:00",
    "is_free": True,
    "audio_url": "/api/podcasts/episode-1/audio?h=free",
}
PROFILE = {
    "alumni": {
        "fio": "Тестовый выпускник",
        "cohort": "2020",
        "verification_status": "verified",
        "contacts": {},
        "interests": [],
        "referral_code": "TEST",
    },
    "level": {"level_title": "Участник", "points": 100, "discount": 5},
    "achievements": [],
    "activity": [{"month": "сен", "points": 60}],
}
ANALYTICS = {
    "pulse": {"joins": 2},
    "snapshot": {"alumni_count": 5, "alumni_verified": 4, "verified_ratio": 80},
    "orders": {
        "paid_sum_kop": 250000,
        "by_type": [{"key": "dpo", "count": 1}],
        "by_status": [{"key": "confirmed", "count": 1}],
        "programs_top": [{"title": "Программа в аналитике", "qty": 2, "orders": 1}],
    },
    "community": {
        "points_by_reason": [{"key": "event", "count": 2}],
        "achievements_top": [{"title": "Первый шаг", "count": 1}],
    },
    "engagement": {
        "events_top": [{"title": "Встреча в аналитике", "rsvps": 3, "attended": 2}],
        "podcasts_top": [{"title": "Выпуск в аналитике", "plays": 4, "listeners": 3}],
    },
    "pageviews": {"hits": 7, "paths_top": [{"path": "/dpo", "count": 4}]},
    "support": {
        "open": 1,
        "created_in_range": 2,
        "by_status": [{"status": "open", "count": 1}],
        "by_topic": [{"topic": "Регистрация", "count": 1}],
    },
    "series": {"joins_by_day": [{"day": "2026-10-01", "count": 2}]},
}


@pytest.fixture
async def browser_client():
    async def upstream(request):
        path = request.url.path
        if path.startswith(("/me", "/auth/admin-session", "/admin")) and "authorization" not in request.headers:
            return httpx.Response(401, json={"error": "Войдите в кабинет"})
        endpoints = {
            "/pages/home": {"blocks": {}},
            "/news": [NEWS],
            "/news/news-one": NEWS,
            "/programs": [PROGRAM],
            "/programs/course-one": PROGRAM,
            "/portfolio": [PORTFOLIO],
            "/portfolio/club-one": PORTFOLIO,
            "/products": [PRODUCT],
            "/events": [EVENT],
            "/podcasts": {"items": [EPISODE], "subscribed": False, "price": 499900},
            "/me": PROFILE,
            "/me/orders": [],
            "/me/classmates": [],
            "/me/events": [],
            "/me/ledger": [],
            "/auth/admin-session": {
                "role": "editor" if request.headers.get("authorization") == "Bearer editor-test" else "admin"
            },
            "/cart": {
                "items": [
                    {
                        "type": "merch",
                        "ref_id": PRODUCT["id"],
                        "title": PRODUCT["title"],
                        "price": PRODUCT["price"],
                        "qty": 2,
                    }
                ],
                "subtotal": 100000,
                "count": 2,
            },
            "/support/config": {"enabled": True, "version": "test-version", "consent": "Тестовое согласие"},
            "/admin/overview": {"alumni_count": 1},
            "/admin/system-health": {"checks": []},
            "/admin/analytics": ANALYTICS,
            "/admin/podcast-subs": {"items": [], "by_podcast": []},
            "/admin/news-sources": {"items": [], "sources": [], "automatic": False},
            "/admin/pages/home": {"blocks": {}},
        }
        if path in endpoints:
            return httpx.Response(200, json=endpoints[path])
        if path.startswith("/admin/"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"error": "Не найдено"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        app = create_app(api)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://web.test") as client:
            yield client


@pytest.mark.parametrize(
    "path",
    list(PUBLIC_PAGES)
    + [
        "/dpo/course-one",
        "/portfolio/club-one",
        "/merch/bag-one",
        "/news/news-one",
        "/events/event-1",
        "/podcasts/episode-1",
    ],
)
async def test_public_pages_render(browser_client, path):
    response = await browser_client.get(path)
    assert response.status_code == 200
    assert '<html lang="ru" class="lux" data-live=' in response.text
    assert 'id="main"' in response.text
    assert "React" not in response.text
    assert response.headers["cache-control"] == (
        "no-cache" if response.headers.get("X-Club-Offline") == "public" else "no-store"
    )


@pytest.mark.parametrize(
    "path,authorized,offline",
    [
        ("/news/news-one", False, True),
        ("/saved", False, True),
        ("/changes/tg-18", False, True),
        ("/dpo", False, True),
        ("/?source=pwa", False, True),
        ("/tg?pwa=1", False, True),
        ("/news?source=other", False, False),
        ("/dpo", True, False),
        ("/news/news-one", True, False),
        ("/news/missing", False, False),
        ("/news?token=private-link", False, False),
        ("/cart", False, False),
        ("/lk/profile", True, False),
        ("/admin", True, False),
        ("/views/news", False, False),
        ("/api/me", True, False),
        ("/reset?token=private-link", False, False),
        ("/podcasts/episode-1", False, False),
    ],
)
async def test_offline_permission_is_only_given_to_anonymous_public_pages(browser_client, path, authorized, offline):
    headers = {"authorization": "Bearer member-test"} if authorized else {}
    response = await browser_client.get(path, headers=headers)
    assert response.headers.get("X-Club-Offline") == ("public" if offline else None)
    assert response.headers["cache-control"] == ("no-cache" if offline else "no-store")


@pytest.mark.parametrize("section", [key for key, _ in OFFICE_NAV])
async def test_office_sections_render(browser_client, section):
    response = await browser_client.get("/views/admin/" + section, headers={"authorization": "Bearer admin-test"})
    assert response.status_code == 200
    assert 'id="main"' in response.text
    assert "Войти в кабинет" not in response.text


@pytest.mark.parametrize(
    "path,root",
    [
        ("/lk", 'data-live="member" data-veil'),
        ("/lk/profile", 'data-live="member" data-veil'),
        ("/admin/news", 'data-live="office" data-veil'),
        ("/cart", 'data-live="cart" data-veil'),
        ("/podcasts", 'data-live="member">'),
        ("/events/event-1", 'data-live="member">'),
        ("/news", 'data-live="">'),
    ],
)
async def test_pages_tell_browser_whether_they_load_member_data(browser_client, path, root):
    response = await browser_client.get(path)
    assert '<html lang="ru" class="lux" ' + root in response.text
    assert 'class="club-skeleton"' in response.text


async def test_guest_cannot_load_private_fragment(browser_client):
    response = await browser_client.get("/views/lk", headers={"authorization": ""})
    assert "Тестовый выпускник" not in response.text
    assert "Вход для выпускников" in response.text


async def test_analytics_keeps_financial_and_engagement_sections(browser_client):
    response = await browser_client.get("/views/admin/analytics", headers={"authorization": "Bearer admin-test"})
    assert response.status_code == 200
    for text in ("2 500", "Программа в аналитике", "Встреча в аналитике", "Выпуск в аналитике", "80%", "Регистрация"):
        assert text in response.text.replace("\xa0", " ").replace("\u202f", " ")


async def test_member_fragment_and_cart(browser_client):
    response = await browser_client.get("/views/lk", headers={"authorization": "Bearer member-test"})
    assert response.status_code == 200
    assert "Тестовый выпускник" in response.text
    response = await browser_client.get("/views/cart", headers={"x-cart-session": "test-session"})
    assert "Сумка клуба" in response.text
    assert "contact_email" in response.text


async def test_editor_does_not_receive_full_admin_controls(browser_client):
    response = await browser_client.get("/views/admin/members", headers={"authorization": "Bearer editor-test"})
    assert response.status_code == 200
    assert "Обезличить" not in response.text
    assert 'href="/admin/support"' in response.text
    response = await browser_client.get("/views/admin/support", headers={"authorization": "Bearer editor-test"})
    assert "Переписку поддержки обрабатывает администратор" in response.text
    assert 'data-api="/admin/support/' not in response.text


async def test_html_is_escaped(browser_client):
    response = await browser_client.get("/news/news-one")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text


async def test_unknown_and_legacy_paths(browser_client):
    assert (await browser_client.get("/not-a-page")).status_code == 404
    response = await browser_client.get("/legacy/news?q=hello")
    assert response.status_code == 308
    assert response.headers["location"] == "/news?q=hello"


@pytest.mark.parametrize(
    "path",
    [
        "/v2/%2fevil.example",
        "/legacy/%2fevil.example",
        "/v2/%5cevil.example",
        "/%2fevil.example/",
        "/v2/%2f%2fevil.example",
    ],
)
async def test_legacy_redirect_rejects_external_destination(browser_client, path):
    response = await browser_client.get(path)
    assert response.status_code == 400
    assert "location" not in response.headers


@pytest.mark.parametrize("path", ["/merch/bag-one", "/events/event-1", "/podcasts/episode-1", "/portfolio/club-one"])
async def test_detail_api_failure_remains_retryable(path):
    async def upstream(request):
        return httpx.Response(503, json={"error": "Сервис временно недоступен"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            response = await client.get(path)
    assert response.status_code == 503
    assert "Не удалось загрузить страницу" in response.text
    assert "Страница не найдена" not in response.text


async def test_failed_home_data_is_not_saved_offline():
    async def upstream(request):
        return httpx.Response(503, json={"error": "Сервис временно недоступен"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            response = await client.get("/?source=pwa")
    assert response.status_code == 200
    assert response.headers.get("X-Club-Offline") is None
    assert response.headers["cache-control"] == "no-store"
    assert "Программы не&nbsp;загрузились" in response.text
    assert "Программ в&nbsp;каталоге" not in response.text
    assert "data-starts-empty" not in response.text


@pytest.mark.parametrize(
    "hero,chars",
    [({}, 8), ({"title_pre": "Клуб выпускников", "title_accent": "факультета права"}, 11)],
)
async def test_home_hero_words_fit_longest_word(hero, chars):
    async def upstream(request):
        if request.url.path == "/pages/home":
            return httpx.Response(200, json={"blocks": {"hero": hero}})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            response = await client.get("/")
    assert f'style="--hero-chars:{chars}"' in response.text


async def test_home_uses_only_sourced_photos(browser_client):
    response = await browser_client.get("/")
    photos = set(re.findall(r"assets/photos/club/([a-z]+)-", response.text))
    assert photos == {"graduates", "meeting", "conversation", "voice"}


async def test_home_texts_follow_glossary(browser_client):
    page = (await browser_client.get("/")).text
    assert '<a href="/portfolio" class="foc lux-more">Программы клуба' in page
    assert '<a href="/events" class="foc lux-more">Все события' in page
    assert (
        "Анонсы встреч, программы дополнительного образования, новости клуба и&nbsp;личный кабинет выпускника." in page
    )
    assert (
        "Подайте заявку, а&nbsp;мы подтвердим ваш статус выпускника и&nbsp;предоставим доступ к&nbsp;новым возможностям."
        in page
    )
    assert "<h3>Проверка</h3><p>Мы подтверждаем статус выпускника факультета.</p>" in page
    assert "Программы клуба и&nbsp;ДПО по&nbsp;цене выпускника, события и&nbsp;подкасты.</p>" in page
    assert "«Друг клуба» с&nbsp;200&nbsp;баллов, «Знаток» с&nbsp;500, «Амбассадор» с&nbsp;1000." in page
    home = (Path(club_web.__file__).parent / "templates/home.html").read_text()
    assert re.findall(".{0,40}[ёЁ–—].{0,20}", home) == []


def test_home_club_programs_put_nearest_open_starts_first():
    today = date(2026, 11, 1)
    rows = [
        {"slug": "closed", "enrollment": "nonactual", "cohorts": [{"starts_on": "2026-11-02"}]},
        {"slug": "undated", "enrollment": "actual", "dates": {}},
        {"slug": "late", "enrollment": "actual", "dates": {"start": "1 декабря 2026"}},
        {"slug": "past", "enrollment": "actual", "dates": {"start": "2026-10-01"}},
        {"slug": "soon", "enrollment": "actual", "cohorts": [{"starts_on": "2026-11-05"}]},
    ]
    assert [row["slug"] for row in club_programs_preview(rows, today)] == ["soon", "late", "undated"]
    assert [row["slug"] for row in club_programs_preview([rows[0], rows[3]], today)] == ["past", "closed"]


async def test_home_shows_club_programs_after_join_steps(browser_client):
    page = (await browser_client.get("/")).text
    assert page.index('id="join-steps"') < page.index('id="club-programs"') < page.index('id="dpo-prices"')
    block = page[page.index('id="club-programs"') : page.index('id="dpo-prices"')]
    assert '<h2 class="lux-h2 lux-reveal" id="lux-portfolio-title">Программы клуба.</h2>' in block
    assert "Курсы дополнительного образования, которые проводит клуб.</p>" in block
    assert '<h3><a href="/portfolio/club-one">Переговоры для юристов</a></h3>' in block
    assert "Смешанный · 6 недель · 1 декабря 2026" in block and "45\u00a0000 ₽" in block
    assert '<a href="/portfolio" class="foc lux-more">Все программы клуба <span' in block
    assert '<h2><a href="/portfolio/club-one">' in (await browser_client.get("/portfolio")).text


@pytest.mark.parametrize(
    "status,payload,text,link",
    [
        (200, [], '<p class="lux-portfolio__note">Программы скоро появятся.</p>', False),
        (
            503,
            {"error": "Сервис временно недоступен"},
            "Программы не&nbsp;загрузились. Обновите страницу или откройте портфель программ.",
            True,
        ),
    ],
)
async def test_home_club_programs_empty_and_failed_states(status, payload, text, link):
    async def upstream(request):
        if request.url.path == "/portfolio":
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json={"blocks": {}} if request.url.path == "/pages/home" else [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            page = (await client.get("/")).text
    assert text in page and "site-catalog-card" not in page
    assert ('class="foc lux-more">Все программы клуба' in page) is link


async def test_full_club_name_and_merch_text(browser_client):
    page = (await browser_client.get("/merch")).text
    assert "<title>Мерч клуба - Клуб выпускников факультета права НИУ ВШЭ</title>" in page
    assert "<small>факультета права НИУ&nbsp;ВШЭ</small>" in page
    assert "© 2026 Клуб выпускников факультета права НИУ&nbsp;ВШЭ" in page
    assert "<p>Одежда и аксессуары с символикой клуба и ВШЭ.</p>" in page


async def test_hidden_changes_section_shows_stub_and_leaves_menus_and_search(browser_client):
    home = (await browser_client.get("/")).text
    assert 'href="/changes"' not in home and "Изменения в&nbsp;праве</h3>" in home
    assert '<p class="lux-tile__lede">Раздел в&nbsp;разработке.</p>' in home
    assert "<p>Программы клуба и ДПО, события, подкасты и новости.</p>" in home
    assert (
        'content="Клуб выпускников факультета права НИУ ВШЭ. Программы клуба и ДПО, события, подкасты и новости."'
        in home
    )
    for path in ("/changes", "/changes/tg-18"):
        page = await browser_client.get(path)
        assert page.status_code == 200 and "<h1>Изменения в праве</h1>\n<p>Раздел в разработке.</p>" in page.text
    search = (await browser_client.get("/search", params={"q": "237-ФЗ"})).text
    assert "Изменения в праве" not in search.split('<main id="main"', 1)[1]
    saved = (await browser_client.get("/saved")).text
    assert '"change"' not in saved and "Изменения в праве" not in saved.split('<main id="main"', 1)[1]


async def test_changes_section_returns_with_one_switch(changes_section, browser_client):
    home = (await browser_client.get("/")).text
    assert 'href="/changes"' in home and "Все изменения" in home
    assert "<p>Программы клуба и ДПО, события, подкасты и изменения в праве.</p>" in home
    assert "Раздел в разработке" not in (await browser_client.get("/changes")).text


async def test_cart_line_has_no_empty_variant(browser_client):
    response = await browser_client.get("/cart", headers={"x-cart-session": "cart-test"})
    assert f"<p>{rub(PRODUCT['price'])}</p>" in response.text


async def test_office_home_form_has_only_fields_shown_on_site(browser_client):
    response = await browser_client.get("/views/admin/pages", headers={"authorization": "Bearer admin-test"})
    names = re.findall(r'name="((?:hero|cta)\.\w+)"', response.text)
    assert names == ["hero.title_pre", "hero.title_accent", "hero.cta_primary", "cta.title", "cta.button"]


async def test_office_uses_dark_theme(browser_client):
    response = await browser_client.get("/admin", headers={"authorization": "Bearer admin-test"})
    assert '<html lang="ru" class="lux" data-live="office" data-veil>' in response.text
    assert "assets/themis-club.svg" in response.text


async def test_office_page_failure_does_not_render_empty_edit_form():
    async def upstream(request):
        return (
            httpx.Response(200, json={"role": "admin"})
            if request.url.path == "/auth/admin-session"
            else httpx.Response(503, json={"error": "Сервис временно недоступен"})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            response = await client.get("/views/admin/pages", headers={"authorization": "Bearer test"})
    assert response.status_code == 200
    assert "Сервис временно недоступен" in response.text
    assert 'data-api="/admin/pages/home"' not in response.text


@pytest.mark.parametrize("value", ["²", "9" * 5000, "-1", "0", "invalid"])
async def test_invalid_pagination_does_not_crash(browser_client, value):
    response = await browser_client.get("/changes", params={"page": value})
    assert response.status_code == 200
    response = await browser_client.get(
        "/views/admin/orders", params={"page": value}, headers={"authorization": "Bearer test"}
    )
    assert response.status_code == 200


async def test_subscription_return_survives_password_recovery(browser_client):
    continuation = "/podcasts#podcast-subscription"
    response = await browser_client.get("/lk", params={"next": continuation})
    assert 'href="/forgot?next=/podcasts%23podcast-subscription"' in response.text
    response = await browser_client.get("/reset", params={"next": continuation, "token": "test"})
    assert 'href="/lk?next=/podcasts%23podcast-subscription"' in response.text
    response = await browser_client.get("/lk", params={"next": "https://external.test"})
    assert 'href="/forgot"' in response.text


async def test_unavailable_changes_source_keeps_saved_materials_and_status(
    changes_section, browser_client, monkeypatch
):
    from club_web import pages

    original = pages.resource
    snapshot = {**original("changes.json"), "syncStatus": "unavailable"}
    monkeypatch.setattr(pages, "resource", lambda name: snapshot if name == "changes.json" else original(name))
    for path in ("/changes", "/changes/tg-18"):
        response = await browser_client.get(path)
        assert response.status_code == 200
        assert "Источник временно недоступен. Показаны сохраненные материалы." in response.text
        assert "Последнее успешное обновление" in response.text


async def test_global_search_finds_club_programs_first(browser_client):
    response = await browser_client.get("/search", params={"q": "переговоры"})
    assert 'href="/portfolio/club-one"' in response.text
    assert response.text.index("Портфель программ</h2>") < response.text.index('href="/portfolio/club-one"')
    assert "Портфель программ</h2>" not in (await browser_client.get("/search", params={"q": "нет такого"})).text


async def test_search_survives_unexpected_portfolio_payload(monkeypatch):
    async def upstream(request):
        payload = {"path": request.url.path} if request.url.path == "/portfolio" else []
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(client)), base_url="http://web.test"
    ) as browser:
        response = await browser.get("/search", params={"q": "переговоры"})
    assert response.status_code == 200 and "<main" in response.text


async def test_global_search_includes_events_and_archived_acts(changes_section, browser_client):
    response = await browser_client.get("/search", params={"q": "Встреча клуба"})
    assert 'href="/events/event-1"' in response.text
    response = await browser_client.get("/search", params={"q": "237-ФЗ"})
    assert 'href="/changes/0001202607040026"' in response.text


async def test_event_month_and_detail_query_upstream_explicitly():
    requests = []

    async def upstream(request):
        requests.append(str(request.url))
        return httpx.Response(200, json=[EVENT])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            assert (await client.get("/events?month=2026-11")).status_code == 200
            assert (await client.get("/events/event-1")).status_code == 200
    assert requests == ["http://api.test/events?month=2026-11", "http://api.test/events?id=event-1"]


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "//example.com/path",
        "https://user:pass@example.com",
        "data:text/html,x",
        "/\\evil.test",
        "/hello\nworld",
    ],
)
def test_unsafe_urls_are_rejected(value):
    assert not safe_url(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://yoomoney.ru/pay",
        "https://yoomoney.ru:invalid/pay",
        "javascript:alert(1)",
        "https://user:pass@yoomoney.ru/pay",
    ],
)
def test_payment_urls_are_restricted(value):
    assert not payment_url(value)


def test_provider_https_redirect_is_preserved():
    assert payment_url("https://payments.example.test/confirmation") == "https://payments.example.test/confirmation"


@pytest.mark.parametrize(
    ("peer", "supplied", "expected"),
    [
        ("172.18.0.4", "spoof, 203.0.113.50", "203.0.113.50"),
        ("203.0.113.7", "198.51.100.2", "203.0.113.7"),
        ("127.0.0.1", "198.51.100.2", "127.0.0.1"),
        ("172.18.0.4", "invalid", None),
    ],
)
def test_web_forwards_ip_only_from_trusted_proxy(peer, supplied, expected):
    from request_helpers import request_from_scope as Request

    from club_web.client import forwarded_headers

    request = Request({"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", supplied.encode())]})
    assert forwarded_headers(request).get("x-forwarded-for") == expected


async def test_root_fragment_can_refresh_home_and_remains_uncacheable(browser_client):
    response = await browser_client.get("/views/")
    assert response.status_code == 200
    assert 'id="page"' in response.text and 'id="main"' in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "x-club-offline" not in response.headers


async def test_cart_forms_send_catalog_type(browser_client):
    response = await browser_client.get("/views/cart", headers={"x-cart-session": "test-session"})
    assert '<input type="hidden" name="type" value="merch">' in response.text
    assert '"type": "merch"' in response.text


async def test_versioned_assets_are_cached_for_a_year(browser_client):
    build_public(PUBLIC)
    version = (PUBLIC / "assets/version.txt").read_text()
    page = (await browser_client.get("/")).text
    for path in ("assets/site.css", "assets/site.js", "fonts/fonts.css"):
        assert f"/{path}?v={version}" in page
    assert page.count('rel="preload"') == 3 and 'href="/fonts/HSESans-Thin.woff2"' in page
    policies = {
        f"/assets/site.css?v={version}": "public, max-age=31536000, immutable",
        f"/assets/home.js?v={version}": "public, max-age=31536000, immutable",
        "/assets/site.css?v=stale": "no-cache",
        "/assets/site.js": "no-cache",
        "/fonts/HSESans-Thin.woff2": "public, max-age=2592000",
        "/assets/themis-club.svg": "public, max-age=86400",
    }
    for path, policy in policies.items():
        response = await browser_client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == policy, path


async def test_portfolio_pages_show_club_program_without_cart(browser_client):
    listing = await browser_client.get("/portfolio")
    assert listing.status_code == 200
    assert 'href="/portfolio/club-one"' in listing.text and "45\u00a0000 ₽" in listing.text
    assert "Смешанный · 6 недель · 1 декабря 2026" in listing.text
    assert 'data-api="/cart"' not in listing.text
    card = await browser_client.get("/portfolio/club-one")
    assert card.status_code == 200
    for text in ("Анна Вымышленная", "Тренер", "Компетенции", "Подготовка позиции", "Коммуникация", "Мест в группе"):
        assert text in card.text
    assert "Сертификат об обучении" in card.text and 'data-reading-kind="portfolio"' in card.text
    assert 'data-api="/cart"' not in card.text and 'href="#application"' in card.text
    assert 'data-api="/portfolio/club-one/applications" data-application' in card.text
    assert (
        "Свободных мест" in card.text and 'max="20"' in card.text and "Мест нет. Заявку можно оставить" not in card.text
    )
    assert 'href="/portfolio-consent"' in card.text and 'href="/portfolio-terms"' in card.text


async def program_page(program):
    async def upstream(request):
        if request.url.path == "/programs/" + program["slug"]:
            return httpx.Response(200, json=program)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return (await client.get("/dpo/" + program["slug"])).text


async def portfolio_page(program):
    async def upstream(request):
        if request.url.path == "/portfolio/" + program["slug"]:
            return httpx.Response(200, json=program)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return (await client.get("/portfolio/" + program["slug"])).text


async def test_portfolio_card_shows_course_modules():
    modules = [
        {
            "title": "День 1. Архитектура конфликта",
            "points": ["Юридический спор"],
            "hours": "6 ак. ч.",
            "format": "Лекция",
        },
        {"title": "Самостоятельная работа", "points": [], "hours": "2 ак. ч.", "format": None},
    ]
    page = await portfolio_page({**PORTFOLIO, "modules": modules})
    assert "<h2>Программа курса</h2>" in page and "<summary>День 1. Архитектура конфликта</summary>" in page
    assert "<li>Юридический спор</li>" in page and "<p>6 ак. ч. · Лекция</p>" in page and "<p>2 ак. ч.</p>" in page
    assert "Программа курса" not in await portfolio_page(PORTFOLIO)
    dpo = await program_page({**PROGRAM, "modules": [{"title": "Вводный модуль", "hours": 0}]})
    assert "<summary>Вводный модуль</summary>" in dpo and "Часов" not in dpo


async def test_dpo_merch_and_cart_texts_follow_glossary(browser_client):
    dpo = (await browser_client.get("/dpo")).text
    merch = (await browser_client.get("/merch")).text
    cart = (await browser_client.get("/cart", headers={"x-cart-session": "cart-test"})).text
    assert '<option value="">Не важно</option>' in dpo and "<p>Стоимость указана без скидки выпускника.</p>" in dpo
    assert '<span class="lux-sr"> о программе «Правовая программа»</span>' in dpo
    assert '<span class="lux-sr"> о товаре «Сумка клуба»</span>' in merch
    assert "Скидка выпускника на программы ДПО" not in cart
    assert "Скидка выпускника действует только на программы ДПО." in cart
    templates = Path(club_web.__file__).parent / "templates"
    for name in ("catalog.html", "program.html", "cart.html", "product.html"):
        assert re.findall(".{0,40}[ёЁ–—].{0,20}", (templates / name).read_text()) == [], name


async def test_dpo_discount_note_only_for_club_applications():
    note = "Скидка выпускника учитывается при оформлении заявки после входа в кабинет."
    open_page = await program_page({**PROGRAM, "modules": [{"title": "Модуль", "hours": 21}]})
    assert note in open_page and "‹</span> Программы ДПО</a>" in open_page and "<p>Часов: 21</p>" in open_page
    assert note not in await program_page({**PROGRAM, "source_url": "https://www.hse.ru/edu/dpo/1"})
    assert note not in await program_page({**PROGRAM, "enrollment": "nonactual"})


async def test_events_podcasts_news_search_saved_texts_follow_glossary(browser_client):
    pages = {
        path: (await browser_client.get(path)).text for path in ("/events", "/podcasts", "/news", "/search", "/saved")
    }
    assert "<p>Баллы за участие: 60</p>" in pages["/events"] and "Файл для календаря (.ics)</a>" in pages["/events"]
    assert "Подкаст клуба. Пробный выпуск открыт всем, остальные доступны по подписке на год." in pages["/podcasts"]
    assert '<span class="club-caps">Пробный выпуск · 30:00</span>' in pages["/podcasts"]
    assert "<p>Новости факультета и клуба.</p>" in pages["/news"]
    assert "<h1>Поиск по сайту</h1>" in pages["/search"] and "<h1>Сохраненное</h1>" in pages["/saved"]
    templates = Path(club_web.__file__).parent / "templates"
    for name in (
        "events.html",
        "podcasts.html",
        "episode.html",
        "news.html",
        "article.html",
        "search.html",
        "saved.html",
        "changes.html",
    ):
        assert re.findall(".{0,40}[ёЁ–—].{0,20}", (templates / name).read_text()) == [], name


async def cabinet_page(profile):
    async def upstream(request):
        return httpx.Response(200, json=profile if request.url.path == "/me" else [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return (await client.get("/views/lk", headers={"authorization": "Bearer member-test"})).text


async def test_join_cabinet_and_profile_texts_follow_glossary(browser_client):
    join = (await browser_client.get("/join")).text
    assert "Как вступить <em>в клуб</em>" in join and "<li>Откроется кабинет</li>" in join
    assert "Пароль, не меньше 8 символов" in join and ">Отправить заявку</button>" in join
    level = {"level_title": "Выпускник", "points": 123, "discount": 5, "next_level": "Друг клуба", "to_next": 77}
    cabinet = await cabinet_page({**PROFILE, "level": level})
    assert "До уровня «Друг клуба» осталось баллов: 77" in cabinet and "<dt>Скидка выпускника</dt>" in cabinet
    assert '<a href="#community">Однокурсники</a>' in cabinet and "<h2>Обзор</h2>" in cabinet
    assert "<h3>Ближайшее событие</h3>" in cabinet and "Ближайших событий пока нет." in cabinet
    assert "> 60</p>" in cabinet and "60 баллов" not in cabinet
    rejected = await cabinet_page({**PROFILE, "alumni": {**PROFILE["alumni"], "verification_status": "rejected"}})
    assert "Мы не смогли подтвердить данные заявки. Если это ошибка, напишите в поддержку." in rejected
    assert "Загрузить фото профиля" not in rejected
    templates = Path(club_web.__file__).parent / "templates"
    for name in ("auth.html", "cabinet.html", "profile.html"):
        assert re.findall(".{0,40}[ёЁ–—].{0,20}", (templates / name).read_text()) == [], name


async def test_portfolio_texts_follow_glossary(browser_client):
    listing = (await browser_client.get("/portfolio")).text
    card = (await browser_client.get("/portfolio/club-one")).text
    assert "<p>Программы дополнительного образования, которые проводит клуб.</p>" in listing
    assert '<span class="lux-sr"> о программе «' in listing
    assert "Промокод вводится в заявке, из двух скидок применяется большая." in card
    assert "Платить на сайте не нужно. Мы свяжемся с вами" in card
    templates = Path(club_web.__file__).parent / "templates"
    for name in ("portfolio.html", "portfolio-program.html"):
        assert re.findall(".{0,40}[ёЁ–—].{0,20}", (templates / name).read_text()) == [], name


async def test_support_texts_follow_glossary(browser_client):
    support = (await browser_client.get("/support")).text
    assert "<summary>Как вступить в клуб?</summary>" in support
    assert "<summary>Чем отличаются ПК и ПП?</summary>" in support and "<summary>Пк" not in support
    assert '<option value="order">Заявка</option>' in support
    assert "Даю согласие на обработку данных обращения</label>" in support
    template = Path(club_web.__file__).parent / "templates" / "support.html"
    assert re.findall(".{0,40}[ёЁ–—].{0,20}", template.read_text()) == []


def test_office_texts_follow_glossary():
    assert (
        office.SECTION_LABELS["cohorts"]["open"] == "Набор открыт" and office.LABELS["expired"] == "Истек срок резерва"
    )
    assert ("starts_at", "Дата и время начала по Москве", "datetime-local") in office.FIELDS["events"]
    assert ("is_free", "Пробный выпуск", "checkbox") in office.FIELDS["podcasts"]
    labels = [office.DESCRIPTIONS, office.SECTION_LABELS, office.FIELDS, office.LABELS, office.METRICS]
    assert re.findall("[ёЁ–—]", repr(labels)) == []
    templates = Path(club_web.__file__).parent / "templates"
    for template in [
        *sorted((templates / "office").glob("*.html")),
        templates / "office.html",
        templates / "themis.html",
    ]:
        assert re.findall(".{0,40}[ёЁ–—].{0,20}", template.read_text()) == [], template.name


async def test_event_form_picks_moscow_date_and_time():
    async def upstream(request):
        if request.url.path == "/auth/admin-session":
            return httpx.Response(200, json={"role": "admin"})
        return httpx.Response(200, json=[EVENT] if request.url.path == "/admin/events" else [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            page = (await client.get("/views/admin/events", headers={"authorization": "Bearer admin-test"})).text
    assert '<input name="starts_at" type="datetime-local" value="2026-11-01T15:00" required>' in page
    assert '<input name="starts_at" type="datetime-local" value="" required>' in page
    assert (local_datetime("2026-11-01T12:00:00Z"), local_datetime(None), local_datetime("скоро")) == (
        "2026-11-01T15:00",
        "",
        "",
    )


async def test_login_search_and_mini_texts_follow_glossary(browser_client):
    login = (await browser_client.get("/lk")).text
    note = "Скидка выпускника и все разделы кабинета откроются, когда мы подтвердим ваш статус."
    assert note in login and "Войти можно после того" not in login
    search = (await browser_client.get("/search")).text
    assert search.count("<label>Программы, события, новости, подкасты<input") == 2
    assert "<p>Баллы, заявки и скидка выпускника.</p>" in (await browser_client.get("/tg")).text


def test_portfolio_start_and_price_follow_open_cohorts():
    cohorts = [{"starts_on": "2026-12-01", "price": 3900000}, {"starts_on": "2027-02-01", "price": 4500000}]
    item = {**PORTFOLIO, "cohorts": cohorts}
    assert (program_start(item), program_price(item)) == ("2026-12-01", "от 39\u00a0000 ₽")
    assert program_price({**PORTFOLIO, "cohorts": cohorts[:1]}) == "39\u00a0000 ₽"
    assert (program_start(PORTFOLIO), program_price(PORTFOLIO)) == ("1 декабря 2026", "45\u00a0000 ₽")
    assert program_start({**PORTFOLIO, "cohorts": {"unexpected": True}}) == "1 декабря 2026"


async def test_portfolio_leads_the_navigation():
    assert NAV[0] == ("/portfolio", "Портфель программ")


async def test_consent_placeholders_show_draft_version(browser_client):
    for path in ("/portfolio-consent", "/portfolio-terms"):
        response = await browser_client.get(path)
        assert response.status_code == 200 and "draft-2026-10-27" in response.text


async def test_unknown_portfolio_program_is_not_found(browser_client):
    response = await browser_client.get("/portfolio/missing")
    assert response.status_code == 404
    assert "Страница не найдена" in response.text


def test_office_search_finds_promo_by_code_and_note():
    rows = [
        {"code": "SPRING-15", "note": "Весенняя акция", "status": "active"},
        {"code": "AUTUMN-10", "status": "disabled"},
    ]
    assert filter_content(rows, {"q": "spring"}) == rows[:1]
    assert filter_content(rows, {"q": "весенняя"}) == rows[:1]
    assert filter_content(rows, {"status": "disabled"}) == rows[1:]
