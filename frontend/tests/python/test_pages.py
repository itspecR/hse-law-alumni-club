import re
from datetime import date
from pathlib import Path

import httpx
import pytest

import club_web
from club_web import office
from club_web.build import build_public
from club_web.catalog import club_programs_preview, price_label
from club_web.formatting import (
    initials,
    local_datetime,
    lowest_price,
    payment_url,
    price_or_pending,
    program_price,
    program_start,
    rub,
    rubles_input,
    safe_url,
)
from club_web.icons import ICONS
from club_web.main import create_app
from club_web.office import filter_content
from club_web.pages import (
    ACHIEVEMENT_STRIP,
    NAV,
    OFFICE_NAV,
    PUBLIC_PAGES,
    activity_chart,
    level_progress,
    ordered_achievements,
    point_program_groups,
    resource,
)
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
    "source_url": "https://dpo.example.test/programs/pravovaya-programma-101.html",
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
GIVE_CONFIG = {"enabled": True, "card": True, "transfer": False, "min": 100, "max": 500000}
DONATIONS_PAGE = {
    "items": [
        {
            "number": "DON-2026-000007",
            "created_at": "2026-11-05T10:00:00Z",
            "amount": 300000,
            "status": "succeeded",
            "name": "Вымышленный Даритель",
            "email": "donor@example.test",
            "alumni_id": "alumni-1",
            "payment_id": "synthetic-pay-7",
            "paid_at": "2026-11-05T10:05:00Z",
        },
        {
            "number": "DON-2026-000006",
            "created_at": "2026-11-04T09:00:00Z",
            "amount": 150000,
            "status": "review",
            "name": None,
            "email": "-",
            "alumni_id": None,
            "payment_id": None,
            "paid_at": None,
        },
    ],
    "total": 2,
    "page": 1,
    "limit": 50,
    "range": "30d",
    "statuses": {"succeeded": "Оплачено", "review": "На проверке"},
    "periods": {"7d": "7 дней", "30d": "30 дней", "all": "Все время"},
    "summary": {"count": 1, "amount": 300000},
}
CAMPAIGN = {
    "id": "camp-1",
    "slug": "intensive",
    "status": "published",
    "title": "Искусство влияния",
    "subtitle": "Имидж, речь, аудитория",
    "goal_title": "Оснащение аудитории",
    "goal_amount": 150000000,
    "extra_amount": 1200000,
    "invite_threshold": 1500000,
    "amounts": [500000, 1500000, 3000050],
    "highlights": [{"value": "6", "label": "часов практики"}],
    "modules": [
        {"title": "Сильное выступление", "time": "10:00-11:30", "note": "Структура", "points": ["Цель", "Финал"]},
        {"title": "Перерыв 15 минут", "time": None, "note": None, "points": []},
    ],
    "steps": [{"title": "Пожертвование", "text": "Любая сумма."}],
    "faq": [{"question": "Это плата за участие?", "answer": "Нет."}],
    "results": ["Строить выступление"],
    "payment_purpose": "на оснащение аудитории",
    "trainer_ids": [],
    "raised": 5200000,
    "percent": 3,
}
PUBLIC_CAMPAIGN = {
    "slug": "intensive",
    "title": "Искусство влияния",
    "subtitle": "Имидж, речь, аудитория",
    "lede": "Клуб собирает средства на оснащение аудитории.",
    "date_text": "Дата уточняется",
    "time_text": "10:00-17:00",
    "place_text": None,
    "duration_text": "6 часов",
    "goal_title": "Оснащение аудитории",
    "goal_text": "Рассказ о цели.",
    "goal_amount": 150000000,
    "raised": 41200000,
    "percent": 27,
    "invite_threshold": 1500000,
    "amounts": [500000, 1500000, 3000000],
    "about_title": "Интенсив для дарителей",
    "about": "Однодневный интенсив.",
    "highlights": [{"value": "6", "label": "часов практики"}],
    "program_title": "День по часам",
    "modules": [
        {"title": "Сильное выступление", "time": "10:00-11:30", "note": "Структура", "points": ["Цель", "Финал"]},
        {"title": "Перерыв 15 минут", "time": None, "note": None, "points": []},
        {"title": "Разбор", "time": "16:25", "note": None, "points": []},
    ],
    "feature_title": "Деловая игра",
    "feature_note": "Финал",
    "feature_text": "Учебная пресс-конференция.",
    "results_title": "Чему учит день",
    "results": ["Строить выступление"],
    "steps": [{"title": "Пожертвование", "text": "Любая сумма."}],
    "faq": [{"question": "Это плата за участие?", "answer": "Нет."}],
    "note": "Участие в интенсиве бесплатное.",
    "cover": None,
    "speakers": [{"id": "t-1", "fio": "Вымышленный Спикер", "photo": None, "bio": "Тренер"}],
}
CAMPAIGN_DONORS = {
    "threshold": 1500000,
    "letter_ready": True,
    "items": [
        {
            "number": "DON-2026-000011",
            "name": "Вымышленный Даритель",
            "email": "donor@example.test",
            "amount": 1500000,
            "paid_at": "2026-11-05T10:05:00Z",
            "invited_at": None,
        },
        {
            "number": "DON-2026-000010",
            "name": None,
            "email": "second@example.test",
            "amount": 5000000,
            "paid_at": "2026-11-04T10:05:00Z",
            "invited_at": "2026-11-06T09:30:00Z",
        },
        {
            "number": "DON-2026-000009",
            "name": None,
            "email": "-",
            "amount": 3000000,
            "paid_at": "2026-01-04T10:05:00Z",
            "invited_at": None,
        },
    ],
}
ADMIN = {"authorization": "Bearer admin-test"}
EDITOR = {"authorization": "Bearer editor-test"}
GIVE_CLOSED = "Прием пожертвований на&nbsp;сайте пока не&nbsp;подключен."
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
        if (
            path.startswith(("/admin/donations", "/admin/audit"))
            and request.headers.get("authorization") == EDITOR["authorization"]
        ):
            return httpx.Response(403, json={"error": "Нужен доступ администратора"})
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
            "/donations/config": GIVE_CONFIG,
            "/campaigns/intensive": PUBLIC_CAMPAIGN,
            "/campaigns/bare": {"slug": "bare", "title": "Пустой сбор", "raised": 0, "percent": 0, "goal_amount": 0},
            "/admin/donations": DONATIONS_PAGE,
            "/admin/portfolio": [PORTFOLIO],
            "/admin/campaigns": [CAMPAIGN],
            "/admin/campaigns/camp-1/donors": CAMPAIGN_DONORS,
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
    assert "Обезличить" not in response.text and 'name="fio"' not in response.text
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
    assert "Программы клуба, ДПО по&nbsp;цене выпускника, события и&nbsp;подкасты.</p>" in page
    assert "Скидка 15% с&nbsp;уровня «Знаток», от&nbsp;500&nbsp;баллов. Личная скидка" in page
    assert "Амбассадор" not in page
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
    assert "Набор открыт · старт 1 декабря 2026" in block and "<dd>Смешанный</dd>" in block
    assert "45\u00a0000 ₽" in block and "Цена выпускника" not in block
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
        f"/assets/themis-alive.webp?v={version}": "public, max-age=31536000, immutable",
        "/assets/site.css?v=stale": "no-cache",
        "/assets/site.js": "no-cache",
        "/fonts/HSESans-Thin.woff2": "public, max-age=2592000",
        "/assets/themis-club.svg": "public, max-age=86400",
    }
    for path, policy in policies.items():
        response = await browser_client.get(path)
        assert response.status_code == 200 and response.headers["cache-control"] == policy, path


@pytest.mark.parametrize("path", ["/", "/tg"])
async def test_themis_poster_url_follows_release_version(browser_client, path):
    build_public(PUBLIC)
    version = (PUBLIC / "assets/version.txt").read_text()
    page = (await browser_client.get(path)).text
    assert f'src="/assets/themis-alive.webp?v={version}"' in page


async def test_portfolio_pages_show_club_program_without_cart(browser_client):
    listing = await browser_client.get("/portfolio")
    assert listing.status_code == 200
    assert 'href="/portfolio/club-one"' in listing.text and "45\u00a0000 ₽" in listing.text
    assert "Набор открыт · старт 1 декабря 2026" in listing.text and "<dd>6 недель</dd>" in listing.text
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


async def portfolio_listing(items):
    async def upstream(request):
        if request.url.path == "/portfolio":
            return httpx.Response(200, json=items)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return (await client.get("/portfolio")).text


async def test_portfolio_program_page_leads_with_facts_and_price():
    page = await portfolio_page({**PORTFOLIO, "competencies": ["Подготовка позиции", "Ведение переговоров"]})
    hero = page[page.index('class="site-masthead portfolio-hero"') : page.index('class="site-panel portfolio-buy"')]
    assert '<p class="program-status program-status--open">Набор открыт · старт 1 декабря 2026</p>' in hero
    for label, value in (("Формат", "Смешанный"), ("Начало", "1 декабря 2026"), ("Свободных мест", "17")):
        assert f"<span>{label}</span></dt><dd>{value}</dd>" in hero
    panel = page[page.index('class="site-panel portfolio-buy"') : page.index('class="portfolio-body"')]
    assert '<p class="lux-price">45\u00a0000 ₽</p>' in panel and "<p>Промокод на скидку вводится в заявке.</p>" in panel
    assert "Цена выпускника" not in page and "выпускникам от" not in page
    assert page.count('class="portfolio-points__icon"') == 3 and "<li>Telegram-чат группы</li>" in page
    assert '<span class="portfolio-trainer__initials" aria-hidden="true">АВ</span>' in page
    assert page.index('class="portfolio-dock"') < page.index('id="application"')
    closed = await portfolio_page({**PORTFOLIO, "enrollment": "nonactual", "price": 0})
    assert "portfolio-dock" not in closed and 'id="application"' not in closed
    assert '<p class="lux-price lux-price--pending">Стоимость уточняется</p>' in closed


async def test_portfolio_cards_show_enrollment_facts_and_price_without_member_discount():
    cohorts = [{"starts_on": "2026-12-01", "price": 3900000}, {"starts_on": "2027-02-01", "price": 0}]
    closed = {**PORTFOLIO, "slug": "club-two", "direction": "", "enrollment": "nonactual", "price": 0, "dates": None}
    page = await portfolio_listing([{**PORTFOLIO, "cohorts": cohorts}, closed])
    first, second = page.split('<article class="site-catalog-card program-card"')[1:]
    assert '<p class="program-status program-status--open">Набор открыт · старт 1 декабря 2026</p>' in first
    assert '<span class="lux-sr">Формат</span></dt><dd>Смешанный</dd>' in first
    assert '<p class="site-price">от 39\u00a0000 ₽</p>' in first and "Цена выпускника" not in first
    assert "<span>Переговоры</span></div>" in first and "site-card-media--empty" not in page
    assert '<p class="program-status">Набор закрыт</p>' in second
    assert '<p class="site-price site-price--pending">Стоимость уточняется</p>' in second
    assert "Цена выпускника" not in second
    assert re.search(r'program-card__tile" aria-hidden="true">\s*<svg[^>]*><path[^>]*/></svg>\s*</div>', second)


async def program_response(program):
    async def upstream(request):
        if request.url.path == "/programs/" + program["slug"]:
            return httpx.Response(200, json=program)
        return httpx.Response(404, json={"error": "Программа не найдена."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return await client.get("/dpo/" + program["slug"])


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


async def test_dpo_merch_and_cart_texts_follow_glossary(browser_client):
    dpo = (await browser_client.get("/dpo")).text
    merch = (await browser_client.get("/merch")).text
    cart = (await browser_client.get("/cart", headers={"x-cart-session": "cart-test"})).text
    assert '<option value="">Не важно</option>' in dpo and "<p>Стоимость указана без скидки выпускника.</p>" in dpo
    assert (
        '<span class="lux-sr"> о программе «Правовая программа» на сайте Центра ДПО, откроется в новой вкладке</span>'
        in dpo
    )
    assert '<span class="lux-sr"> о товаре «Сумка клуба»</span>' in merch
    assert "Скидка выпускника на программы ДПО" not in cart
    assert "Скидка выпускника на мерч не распространяется." in cart
    templates = Path(club_web.__file__).parent / "templates"
    for name in ("catalog.html", "cart.html", "product.html"):
        assert re.findall(".{0,40}[ёЁ–—].{0,20}", (templates / name).read_text()) == [], name


async def test_dpo_program_address_leads_to_center_site():
    response = await program_response(PROGRAM)
    assert response.status_code == 302 and response.headers["location"] == PROGRAM["source_url"]
    for source in (None, "javascript:alert(1)", "/dpo/other"):
        assert (await program_response({**PROGRAM, "source_url": source})).status_code == 404


async def test_dpo_catalog_links_to_center_site_with_alumni_price(browser_client):
    page = (await browser_client.get("/dpo")).text
    link = f'href="{PROGRAM["source_url"]}" target="_blank" rel="noopener noreferrer"'
    assert page.count(link) == 2 and '<a href="/dpo/course-one"' not in page
    assert "Скидка выпускника по уровню в клубе. Подробности при оформлении на сайте Центра ДПО." in page
    assert '<p class="site-price">3\u00a0000 ₽</p>' in page and "<p>Цена выпускника от&nbsp;2\u00a0550 ₽</p>" in page
    assert '<span class="lux-more__arrow" aria-hidden="true">↗</span>' in page


async def test_dpo_search_and_home_lead_to_center_site(browser_client):
    search = (await browser_client.get("/search", params={"q": "правовая"})).text
    row = f'href="{PROGRAM["source_url"]}" target="_blank" rel="noopener noreferrer">Правовая программа<span class="lux-sr">'
    assert row in search
    home = (await browser_client.get("/")).text
    assert f'<a class="foc lux-prog__link" href="{PROGRAM["source_url"]}" target="_blank"' in home
    assert "Подробности при&nbsp;оформлении на&nbsp;сайте Центра ДПО.</p>" in home


def test_dpo_price_labels_follow_center_catalog():
    assert (price_label(None), price_label(0), price_label(300000)) == (
        "Стоимость уточняется",
        "Бесплатно",
        "3\u00a0000 ₽",
    )


async def test_office_member_point_history_shows_reasons_and_closes():
    history = {
        "id": "member-1",
        "fio": "Вымышленный Выпускник",
        "points": 140,
        "items": [
            {"id": "a", "delta": 120, "reason": "mentorship", "comment": "Наставничество: Поток 2024"},
            {"id": "b", "delta": 30, "reason": "manual", "comment": "Начисление клуба"},
            {"id": "c", "delta": -10, "reason": "decay", "comment": None},
        ],
    }
    endpoints = {
        "/auth/admin-session": {"role": "admin"},
        "/admin/members": {"items": [{"id": "member-1", "fio": "Вымышленный Выпускник", "points_cached": 140}]},
        "/admin/members/member-1/points": history,
    }

    async def upstream(request):
        return httpx.Response(200, json=endpoints.get(request.url.path, []))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            headers = {"authorization": "Bearer admin-test"}
            page = (await client.get("/views/admin/members?id=member-1", headers=headers)).text
            plain = (await client.get("/views/admin/members", headers=headers)).text
    assert "История баллов: Вымышленный Выпускник" in page and "Сейчас 140 баллов." in page
    assert "Наставничество младшего потока</span><span>Наставничество: Поток 2024</span><strong>+120</strong>" in page
    assert "Начисление клуба</span><span></span><strong>+30</strong>" in page
    assert "Списание за неактивность</span><span></span><strong>-10</strong>" in page
    assert '<a href="/admin/members?id=member-1">История баллов</a>' in plain and "office-history" not in plain
    assert "<summary>Исправить ФИО</summary>" in plain
    assert 'name="fio" value="Вымышленный Выпускник" required' in plain


async def test_office_dpo_section_is_read_only_with_sync_status():
    error = "Сайт ДПО вернул неполный каталог. Список программ не изменен."
    sync = {"address": True, "enabled": True, "minute": 41, "run": {"checked_at": "2026-10-29T10:00:00Z", "found": 34}}
    sync["run"]["error"] = error
    endpoints = {
        "/auth/admin-session": {"role": "admin"},
        "/admin/programs": [{**PROGRAM, "status": "published"}],
        "/admin/dpo-sync": sync,
    }

    async def upstream(request):
        return httpx.Response(200, json=endpoints.get(request.url.path, []))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            page = (await client.get("/views/admin/programs", headers={"authorization": "Bearer admin-test"})).text
            sync["address"] = False
            off = (await client.get("/views/admin/programs", headers={"authorization": "Bearer admin-test"})).text
    assert "Создать запись" not in page and "Редактировать" not in page and 'data-method="DELETE"' not in page
    assert "Каталог обновляется с сайта Центра ДПО каждый час, вручную программы не меняются." in page
    assert "программ: 34." in page and f'<p class="office-source-error">{error}</p>' in page
    assert (
        f'<a href="{PROGRAM["source_url"]}" target="_blank" rel="noopener noreferrer">Открыть на сайте Центра ДПО ↗</a>'
        in page
    )
    assert "Адрес сайта Центра ДПО не задан на сервере, каталог не обновляется." in off


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


def test_point_programs_are_grouped_club_first():
    rows = [
        {"id": "d", "title": "Медиация", "catalog": "dpo"},
        {"id": "c", "title": "Переговоры", "catalog": "portfolio"},
    ]
    assert point_program_groups(rows) == [("Программы клуба", [rows[1]]), ("Программы ДПО", [rows[0]])]


def test_every_named_icon_exists():
    templates = Path(club_web.__file__).parent / "templates"
    named = {name for path in templates.rglob("*.html") for name in re.findall(r"icon\('([a-z-]+)'", path.read_text())}
    assert named and named <= ICONS.keys()
    assert {key for key, _ in OFFICE_NAV} <= ICONS.keys()


@pytest.mark.parametrize(
    "level,expected",
    [
        ({"level": "graduate", "next_level": "Друг клуба", "to_next": 200}, 0),
        ({"level": "friend", "next_level": "Знаток", "to_next": 180}, 40),
        ({"level": "ambassador", "next_level": None, "to_next": 0}, 100),
    ],
)
def test_level_progress_is_share_of_current_level(level, expected):
    levels = [
        {"key": "graduate", "title": "Выпускник", "min_points": 0},
        {"key": "friend", "title": "Друг клуба", "min_points": 200},
        {"key": "expert", "title": "Знаток", "min_points": 500},
        {"key": "ambassador", "title": "Амбассадор", "min_points": 1000},
    ]
    assert level_progress(level, levels) == expected


def test_activity_chart_hides_empty_half_year():
    assert activity_chart([{"month": "сен", "points": 0}, {"month": "окт", "points": 0}]) == []
    chart = activity_chart([{"month": "сен", "points": 50}, {"month": "окт", "points": 200}])
    assert [month["share"] for month in chart] == [25, 100]


@pytest.mark.parametrize(("fio", "expected"), [("Анна Смирнова", "АС"), ("иван", "И"), ("", ""), (None, "")])
def test_initials_take_two_first_letters(fio, expected):
    assert initials(fio) == expected


def achievement(title, current, target):
    return {
        "title": title,
        "description": "Описание " + title,
        "current": current,
        "target": target,
        "earned": current >= target,
    }


def test_achievements_show_earned_then_closest_first():
    items = [
        achievement("Ноль", 0, 3),
        achievement("Треть", 1, 3),
        achievement("Печать", 1, 1),
        achievement("Половина", 1, 2),
    ]
    assert [item["title"] for item in ordered_achievements(items)] == ["Печать", "Половина", "Треть", "Ноль"]


async def test_cabinet_groups_achievements_into_strip_and_grid():
    items = [achievement(f"Цель {number}", 0, 3) for number in range(5)] + [achievement("Печать клуба", 1, 1)]
    cabinet = await cabinet_page({**PROFILE, "achievements": items})
    assert '<a href="#achievements">1 из 6</a>' in cabinet and "Получено 1 из 6." in cabinet
    strip = cabinet.split('aria-label="Ближайшие достижения">')[1].split("</ul>")[0]
    assert strip.count("<li") == ACHIEVEMENT_STRIP and strip.index("Печать клуба") < strip.index("Цель 0")
    grid = cabinet.split('<ul class="cabinet-achievements">')[1].split("</ul>")[0]
    assert grid.count("<details") == len(items) and "Описание Цель 4" in grid
    empty = await cabinet_page(PROFILE)
    assert "Достижения появятся по мере участия в клубе." in empty and "Ближайшие достижения" not in empty


async def test_join_cabinet_and_profile_texts_follow_glossary(browser_client):
    join = (await browser_client.get("/join")).text
    assert "Как вступить <em>в клуб</em>" in join and "<li>Откроется кабинет</li>" in join
    assert "Пароль, не меньше 8 символов" in join and ">Отправить заявку</button>" in join
    level = {"level_title": "Выпускник", "points": 123, "discount": 10, "next_level": "Друг клуба", "to_next": 77}
    cabinet = await cabinet_page({**PROFILE, "level": level})
    assert "До уровня «Друг клуба» осталось баллов: 77" in cabinet and "Скидка выпускника</dt>" in cabinet
    assert '<dd>10%<span class="cabinet-metric__note">на программы ДПО</span></dd>' in cabinet
    assert re.search(r'href="#community">.*?Однокурсники</a>', cabinet, re.DOTALL) and "<h2>Обзор</h2>" in cabinet
    assert "Ближайшее событие</h3>" in cabinet and "Ближайших событий пока нет." in cabinet
    assert "<span>60</span>" in cabinet and "60 баллов" not in cabinet
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
    assert "Промокод на скидку вводится в заявке." in card and "из двух скидок" not in card
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


WEBP_ALPHA_FLAG = 0x10
WEBP_METADATA_FLAGS = 0x20 | 0x08 | 0x04


def webp_canvas(path):
    data = path.read_bytes()
    assert data[:4] == b"RIFF" and data[8:16] == b"WEBPVP8X"
    width = 1 + int.from_bytes(data[24:27], "little")
    height = 1 + int.from_bytes(data[27:30], "little")
    return width, height, data[20]


def test_themis_poster_matches_animation_frame():
    package = Path(club_web.__file__).parent
    width, height, flags = webp_canvas(PUBLIC / "assets/themis-alive.webp")
    source = re.search(r"const SOURCE = \[\d+, \d+, (\d+), (\d+)\]", (package / "browser/themis.js").read_text())
    ratio = re.search(r"--themis-ratio: (\d+) / (\d+)", (package / "styles/themis.css").read_text())
    assert f'width="{width}" height="{height}"' in (package / "templates/themis.html").read_text()
    assert tuple(map(int, source.groups())) == tuple(map(int, ratio.groups())) == (width, height)
    assert flags & WEBP_ALPHA_FLAG and not flags & WEBP_METADATA_FLAGS


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


def test_lowest_price_takes_cheapest_paid_price():
    cohorts = [{"price": 0}, {"price": 4100000}, {"price": 3900000}]
    assert lowest_price({**PORTFOLIO, "cohorts": cohorts}) == 3900000
    assert (lowest_price(PORTFOLIO), lowest_price(PROGRAM)) == (4500000, 300000)
    assert lowest_price({**PORTFOLIO, "price": None}) == lowest_price({**PORTFOLIO, "cohorts": [{"price": 0}]}) == 0


def test_portfolio_price_without_amount_is_pending():
    for item in ({**PORTFOLIO, "price": 0}, {**PORTFOLIO, "price": None}, {**PORTFOLIO, "cohorts": [{"price": 0}]}):
        assert program_price(item) == "Стоимость уточняется"
    assert program_price({**PORTFOLIO, "cohorts": [{"price": 0}, {"price": 3900000}]}) == "от 39\u00a0000 ₽"
    assert (price_or_pending(0), price_or_pending(3900000)) == ("Стоимость уточняется", "39\u00a0000 ₽")


async def test_portfolio_leads_the_navigation():
    assert NAV[0] == ("/portfolio", "Портфель программ")


async def profile_page(profile):
    async def upstream(request):
        return httpx.Response(200, json=profile if request.url.path == "/me" else [])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return (await client.get("/views/lk/profile", headers={"authorization": "Bearer member-test"})).text


async def test_mailing_consent_page_names_operator_and_how_to_withdraw(browser_client):
    page = (await browser_client.get("/mailing-consent")).text
    assert "<h1>Согласие на получение рассылок</h1>" in page and "Редакция от 5 ноября 2026 года" in page
    assert "других инициативах АНО «Клуб выпускников факультета Права НИУ ВШЭ»*" in page
    assert "до 5 (Пяти) рабочих дней" in page and "сняв отметку «Получать рассылки»" in page
    assert "НИУ ВШЭ*" not in page.replace("ВШЭ»*", "")


async def test_join_offers_optional_unchecked_mailing_consent(browser_client):
    page = (await browser_client.get("/join")).text
    field = re.search(r'<input type="checkbox" name="consent_mailing"[^>]*>', page)[0]
    assert "required" not in field and "checked" not in field
    assert '<a href="/mailing-consent">Согласие на получение рассылок</a>, необязательно' in page


async def test_profile_shows_current_mailing_consent():
    for given in (True, False):
        alumni = {**PROFILE["alumni"], "verification_status": "verified", "mailing_consent": given}
        page = await profile_page({**PROFILE, "alumni": alumni})
        field = re.search(r'<input type="checkbox" name="mailing_consent"[^>]*>', page)[0]
        assert ("checked" in field) is given


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


async def test_give_form_takes_bounds_from_server_and_waits_for_documents(browser_client):
    page = (await browser_client.get("/give")).text
    assert 'data-min="100" data-max="500000"' in page
    assert "data-give-submit>" in page and GIVE_CLOSED not in page
    assert 'value="monthly" disabled><span>Каждый месяц <small>Скоро</small>' in page
    assert '<a href="/give/offer">' in page and '<a href="/give/consent">' in page
    assert 'content="index,follow"' in page
    for path in ("/give/offer", "/give/consent"):
        document = (await browser_client.get(path)).text
        assert "<p>Текст готовится.</p>" in document and 'content="noindex,nofollow"' in document


async def give_page(status, config):
    async def upstream(request):
        if request.url.path == "/donations/config":
            return httpx.Response(status, json=config)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return (await client.get("/give")).text


@pytest.mark.parametrize(
    ("status", "config", "closed"),
    [(200, {"enabled": False}, True), (503, {"error": "Сервис временно недоступен"}, False)],
)
async def test_give_form_is_disabled_without_payments(status, config, closed):
    page = await give_page(status, config)
    assert "data-give-submit disabled>" in page
    assert (GIVE_CLOSED in page) is closed


@pytest.mark.parametrize(
    ("card", "transfer", "choice", "method"),
    [(True, False, False, "card"), (False, True, False, "transfer"), (True, True, True, "card")],
)
async def test_give_form_offers_only_available_methods(card, transfer, choice, method):
    page = await give_page(200, {**GIVE_CONFIG, "card": card, "transfer": transfer})
    assert ('<legend class="give-group__label">Как оплатить</legend>' in page) is choice
    assert ('name="method" value="card" checked><span>Картой</span>' in page) is choice
    assert ('name="method" value="transfer"><span>По QR-коду</span>' in page) is choice
    assert (f'<input type="hidden" name="method" value="{method}">' in page) is not choice
    assert f'data-give-hint="{method}">' in page
    other = "transfer" if method == "card" else "card"
    assert f'data-give-hint="{other}" hidden>' in page
    assert "data-give-submit>" in page and "data-give-transfer" in page


async def test_give_is_reachable_from_header_menu_footer_and_home(browser_client):
    home = (await browser_client.get("/")).text
    assert '<a href="/give" class="foc club-header__give" aria-label="Поддержать факультет"' in home
    assert '<a href="/give" class="club-menu__give">Поддержать факультет</a>' in home
    assert '<li>\n<a href="/give">Поддержать факультет</a>\n</li>' in home
    assert '<a href="/give" class="foc lux-btn lux-btn--ghost">Поддержать факультет</a>' in home


async def test_give_thanks_is_generic_and_hidden_from_search(browser_client):
    page = (await browser_client.get("/give/thanks?d=DON-2026-000001")).text
    assert "<h1" in page and "Спасибо!" in page and "DON-2026-000001" not in page
    assert 'content="noindex,nofollow"' in page


async def test_header_with_all_sections_switches_to_dense_layout(changes_section, browser_client):
    page = (await browser_client.get("/news")).text
    assert '<nav class="club-header__nav club-header__nav--dense" aria-label="Разделы">' in page


async def test_header_with_default_sections_keeps_roomy_layout(browser_client):
    page = (await browser_client.get("/news")).text
    assert '<nav class="club-header__nav" aria-label="Разделы">' in page


def plain(text):
    return text.replace("\xa0", " ").replace("\u202f", " ")


async def test_full_admin_sees_donations_read_only_with_paid_total(browser_client):
    page = plain((await browser_client.get("/views/admin/donations?status=review&q=don", headers=ADMIN)).text)
    assert 'href="/admin/donations"' in page
    assert (
        "<h2>DON-2026-000007 · 3 000 ₽</h2>" in page
        and '<span class="office-status" data-status="review">На проверке</span>' in page
    )
    assert "<p>Вымышленный Даритель · donor@example.test · выпускник</p>" in page and "<p>Без имени · -</p>" in page
    assert "<strong>1</strong><span>Оплачено пожертвований за период</span>" in page
    assert "<strong>3 000 ₽</strong><span>Получено за период</span>" in page
    assert 'data-download="/admin/donations/export.csv?q=don&amp;status=review&amp;range=30d"' in page
    assert '<option value="30d" selected>30 дней</option>' in page
    assert 'data-api="/admin/donations' not in page


AWAITING_TRANSFER = {
    **DONATIONS_PAGE["items"][1],
    "number": "DON-2026-000008",
    "status": "awaiting",
    "method": "transfer",
    "amount": 300000,
    "email": "donor@example.test",
}


async def office_donations(listing, query="", asked=None):
    async def upstream(request):
        if request.url.path == "/auth/admin-session":
            return httpx.Response(200, json={"role": "admin"})
        if request.url.path == "/admin/donations":
            if asked is not None:
                asked.append(dict(request.url.params))
            return httpx.Response(200, json=listing)
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return plain((await client.get("/views/admin/donations" + query, headers=ADMIN)).text)


async def test_donations_are_filtered_and_exported_by_campaign():
    campaigns = {"camp-1": "Искусство влияния", "camp-2": "Другой сбор"}
    linked = {**DONATIONS_PAGE["items"][0], "campaign_id": "camp-1", "invited_at": "2026-11-06T09:30:00Z"}
    orphan = {**DONATIONS_PAGE["items"][1], "campaign_id": "camp-9"}
    asked = []
    listing = {**DONATIONS_PAGE, "items": [linked, orphan], "campaigns": campaigns}
    page = await office_donations(listing, "?campaign=camp-1&range=all", asked)
    assert asked == [{"range": "all", "campaign": "camp-1"}]
    assert '<label>Сбор<select aria-label="Сбор" name="campaign">' in page
    assert '<option value="camp-1" selected>Искусство влияния</option>' in page
    assert '<option value="camp-2" >Другой сбор</option>' in page
    assert " · сбор «Искусство влияния» · приглашение отправлено 6 ноября" in page
    assert " · сбор «удален»</p>" in page
    assert 'export.csv?q=&amp;status=&amp;range=30d&amp;campaign=camp-1"' in page


async def test_donations_without_campaigns_have_no_campaign_filter():
    page = await office_donations(DONATIONS_PAGE)
    assert 'name="campaign"' not in page and "сбор «" not in page and "campaign=" not in page


async def test_only_awaiting_transfer_has_received_button():
    statuses = {**DONATIONS_PAGE["statuses"], "awaiting": "Ожидает перевода"}
    page = await office_donations(
        {**DONATIONS_PAGE, "items": [AWAITING_TRANSFER, *DONATIONS_PAGE["items"]], "statuses": statuses}
    )
    assert '<span class="office-status" data-status="awaiting">Ожидает перевода</span>' in page
    assert page.count(" · перевод по реквизитам") == 1
    assert page.count('data-api="/admin/donations/') == 1
    assert (
        '<button data-api="/admin/donations/DON-2026-000008/received" data-confirm="Перевод 3 000 ₽ по пожертвованию '
        'DON-2026-000008 поступил на счет клуба? Дарителю уйдет письмо с благодарностью.">Перевод получен</button>'
    ) in page


PAY_TOKEN = "A" * 43
PAYMENT = {
    "number": "ALU-2026-000042",
    "program": "Переговоры",
    "paid": False,
    "transfer": {
        "amount": 13500000,
        "purpose": "Оплата участия в программе «Переговоры», заявка ALU-2026-000042",
        "payee": [
            {"label": "Получатель", "value": "АНО «Вымышленный клуб»"},
            {"label": "Расчетный счет", "value": "40703810000000000006"},
        ],
        "qr_svg": "data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C%2Fsvg%3E",
        "qr_png": "data:image/png;base64,iVBORw0KGgo=",
    },
}
PROGRAM_ORDER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "number": "ALU-2026-000042",
    "type": "portfolio",
    "contact_fio": "Вымышленный Заявитель",
    "contact_email": "applicant@example.test",
    "contact_phone": "+70000000000",
    "status": "new",
    "payment_status": None,
    "subtotal": 13500000,
    "total_estimate": 13500000,
    "member_discount": 0,
    "participants": 3,
    "items_json": [{"title": "Переговоры", "qty": 3}],
}


async def rendered(path, routes, headers=None):
    async def upstream(request):
        status, body = routes.get(request.url.path, (200, []))
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return await client.get(path, headers=headers or {})


async def test_payment_page_shows_qr_and_requisites_and_stays_private():
    response = await rendered("/pay/" + PAY_TOKEN, {"/transfers/" + PAY_TOKEN: (200, PAYMENT)})
    page = plain(response.text)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert "x-club-offline" not in response.headers and 'content="noindex,nofollow"' in page
    assert "Заявка ALU-2026-000042 на&nbsp;программу «Переговоры»." in page
    assert '<h2 class="give-transfer__title" id="pay-state">Переведите 135 000 ₽</h2>' in page
    assert f'src="{PAYMENT["transfer"]["qr_svg"]}"' in page
    assert 'href="data:image/png;base64,iVBORw0KGgo=" download="ALU-2026-000042.png">Сохранить QR-код</a>' in page
    assert "<dt>Расчетный счет</dt><dd>40703810000000000006</dd>" in page
    assert f"<dt>Назначение платежа</dt><dd>{PAYMENT['transfer']['purpose']}</dd>" in page


@pytest.mark.parametrize(
    ("reply", "status", "heading"),
    [
        ((200, {"number": "ALU-2026-000042", "program": "Переговоры", "paid": True}), 200, "Оплата получена"),
        ((404, {"error": "Ссылка на оплату не найдена или устарела."}), 404, "Ссылка не&nbsp;действует"),
        ((410, {"error": "Заявка закрыта."}), 410, "Заявка закрыта"),
        ((500, {"error": "Сбой"}), 503, "Не&nbsp;удалось загрузить данные для оплаты"),
    ],
)
async def test_payment_page_states(reply, status, heading):
    response = await rendered("/pay/" + PAY_TOKEN, {"/transfers/" + PAY_TOKEN: reply})
    page = plain(response.text)
    assert response.status_code == status and f'id="pay-state">{heading}</h2>' in page
    assert "Сохранить QR-код" not in page and 'class="site-errors"' not in page


@pytest.mark.parametrize(
    ("order", "role", "send", "receive"),
    [
        (PROGRAM_ORDER, "admin", "Отправить реквизиты", False),
        (
            {**PROGRAM_ORDER, "payment_status": "awaiting", "transfer_sent_at": "2026-11-08T10:00:00Z"},
            "admin",
            "Отправить реквизиты повторно",
            True,
        ),
        (
            {**PROGRAM_ORDER, "payment_status": "awaiting", "transfer_sent_at": "2026-11-08T10:00:00Z"},
            "editor",
            "Отправить реквизиты повторно",
            False,
        ),
        ({**PROGRAM_ORDER, "total_estimate": 0}, "admin", None, False),
        ({**PROGRAM_ORDER, "payment_status": "succeeded"}, "admin", None, False),
        ({**PROGRAM_ORDER, "status": "canceled"}, "admin", None, False),
        ({**PROGRAM_ORDER, "type": "merch"}, "admin", None, False),
    ],
)
async def test_order_payment_buttons_follow_state_and_role(order, role, send, receive):
    routes = {"/auth/admin-session": (200, {"role": role}), "/admin/orders": (200, {"items": [order], "total": 1})}
    page = plain((await rendered("/views/admin/orders", routes, ADMIN if role == "admin" else EDITOR)).text)
    sends = re.findall(r'data-api="/admin/orders/[^"]+/transfer" data-confirm="[^"]+">([^<]+)</button>', page)
    assert sends == ([send] if send else [])
    assert ('data-api="/admin/orders/' + order["id"] + '/transfer-received"' in page) is receive
    if receive:
        assert 'data-confirm="Оплата 135 000 ₽ по заявке ALU-2026-000042 поступила на счет клуба?' in page
    if order.get("transfer_sent_at"):
        assert "реквизиты отправлены" in page


async def test_editor_has_no_audit_section(browser_client):
    overview = (await browser_client.get("/views/admin", headers=EDITOR)).text
    admin = (await browser_client.get("/views/admin", headers=ADMIN)).text
    assert 'href="/admin/audit"' not in overview and 'href="/admin/audit"' in admin
    audit = (await browser_client.get("/views/admin/audit", headers=EDITOR)).text
    assert "Нужен доступ администратора" in audit and 'class="site-errors"' not in audit


async def test_editor_has_no_donations_section(browser_client):
    orders = (await browser_client.get("/views/admin/orders", headers=EDITOR)).text
    assert 'href="/admin/orders"' in orders and 'href="/admin/donations"' not in orders
    donations = (await browser_client.get("/views/admin/donations", headers=EDITOR)).text
    assert "Нужен доступ администратора" in donations and "DON-2026" not in donations
    assert 'class="site-errors"' not in donations


def test_rubles_input_keeps_exact_kopecks():
    assert [rubles_input(value) for value in (None, 0, 4500000, 1234567, 123456789, 5)] == [
        "0",
        "0",
        "45000",
        "12345.67",
        "1234567.89",
        "0.05",
    ]


async def test_program_editor_pages_for_existing_new_and_missing_program(browser_client):
    existing = (await browser_client.get("/views/admin/portfolio?id=club-1", headers=ADMIN)).text
    assert 'data-api="/admin/portfolio/club-1" data-method="PATCH"' in existing
    assert '<a href="/admin/portfolio">Портфель программ</a>' in existing and "office-page-header--record" in existing
    assert 'data-action="refresh"' not in existing.split('id="main"', 1)[1].split("</header>", 1)[0]
    assert 'data-method="DELETE" data-next="/admin/portfolio"' in existing
    assert [
        block in existing
        for block in ('id="program-main"', 'id="program-about"', 'id="program-course"', 'id="program-publish"')
    ] == [True] * 4
    created = (await browser_client.get("/views/admin/portfolio?id=new", headers=ADMIN)).text
    assert 'data-method="POST" data-open-record="/admin/portfolio?id="' in created
    assert ">Создать программу</button>" in created and "Удаление программы" not in created
    missing = (await browser_client.get("/views/admin/portfolio?id=unknown", headers=ADMIN)).text
    assert "Программа не найдена: возможно, ее удалили." in missing and "data-program-editor" not in missing
    listing = (await browser_client.get("/views/admin/portfolio", headers=ADMIN)).text
    assert 'href="/admin/portfolio?id=new">' in listing and 'href="/admin/portfolio?id=club-1">Открыть</a>' in listing


TEASER = {
    key: PUBLIC_CAMPAIGN[key]
    for key in ("slug", "title", "subtitle", "lede", "date_text", "place_text", "goal_title", "goal_amount", "cover")
} | {"raised": 41200000, "percent": 27}


async def home_with_campaign(reply):
    async def upstream(request):
        if request.url.path == "/campaigns/current":
            return reply
        if request.url.path == "/pages/home":
            return httpx.Response(200, json={"blocks": {}})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            response = await client.get("/")
            return response.status_code, plain(response.text)


async def test_home_shows_current_campaign_after_hero():
    status, page = await home_with_campaign(httpx.Response(200, json={"campaign": TEASER}))
    assert status == 200
    teaser = page.split('<section class="lux-section camp-teaser-section"', 1)[1].split("</section>", 1)[0]
    assert page.index("lux-hero") < page.index("camp-teaser-section") < page.index('id="join-steps"')
    assert '<h2 class="camp-teaser__title" id="camp-teaser-title">Искусство влияния</h2>' in teaser
    assert "<b>412 000 ₽</b>" in teaser and "из 1 500 000 ₽" in teaser and 'aria-valuenow="27"' in teaser
    assert '<a href="/give/intensive" class="foc lux-btn">' in teaser
    assert '<span class="camp-teaser__facts">Дата уточняется</span>' in teaser
    assert teaser.count('class="camp-wave__bar"') == 56


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, json={"campaign": None}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"campaign": {"title": "Без адреса"}}),
        httpx.Response(404, json={"error": "Не найдено"}),
        httpx.Response(503, json={"error": "Временно недоступно"}),
    ],
)
async def test_home_without_campaign_stays_as_is(reply):
    status, page = await home_with_campaign(reply)
    assert status == 200 and "camp-" not in page and 'id="join-steps"' in page
    assert 'class="site-errors"' not in page and "Временно недоступно" not in page


async def test_campaign_page_shows_story_meter_and_campaign_form(browser_client):
    response = await browser_client.get("/give/intensive")
    page = plain(response.text)
    assert response.status_code == 200 and "<title>Искусство влияния" in page
    assert '<h1 class="camp-hero__title lux-reveal" id="camp-title" style="--d:1">Искусство влияния</h1>' in page
    assert page.count('class="camp-wave__bar"') == 56
    facts = re.search(r'<dl class="camp-facts.*?</dl>', page, re.S)[0]
    assert re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", facts) == [
        ("Когда", "Дата уточняется"),
        ("Время", "10:00-17:00"),
        ("Длительность", "6 часов"),
    ]
    assert page.count("<b>412 000 ₽</b>") == 2 and page.count("из 1 500 000 ₽") == 2
    assert page.count('aria-valuenow="27"') == 2 and page.count('style="--camp-fill:27%"') == 2
    assert '<li class="camp-day__pause lux-reveal"><span>Перерыв 15 минут</span></li>' in page
    assert page.count('<details class="lux-disclosure camp-day__body"') == 1
    assert '<div class="camp-day__body"><span class="camp-day__head"><b>Разбор</b></span></div>' in page
    assert '<h3 id="camp-game-title">Деловая игра</h3>' in page and ">Чему учит день</h2>" in page
    assert "<h3>Вымышленный Спикер</h3><p>Тренер</p>" in page
    assert '<summary class="foc">Это плата за участие?</summary><p>Нет.</p>' in page
    assert '<p class="camp-panel-note">Участие в интенсиве бесплатное.</p>' in page
    form = page.split("<form", 1)[1].split("</form>", 1)[0]
    assert '<p class="camp-panel-head__goal">Оснащение аудитории</p>' in form
    assert re.findall(r'name="preset" value="(\d+)"( checked)?', form) == [
        ("500000", ""),
        ("1500000", " checked"),
        ("3000000", ""),
    ]
    assert '<input type="checkbox" name="consent_gift" required>' in form
    assert "Понимаю, что пожертвование безвозмездно и не является оплатой участия в мероприятиях" in plain(
        form.replace("&nbsp;", " ")
    )
    assert '<input type="hidden" name="campaign" value="intensive">' in form
    assert page.index('class="give-story camp-story"') < page.index('id="camp-support"')
    assert 'content="noindex,nofollow"' in page


async def test_campaign_page_hides_empty_blocks_and_unknown_campaign_is_missing(browser_client):
    bare = plain((await browser_client.get("/give/bare")).text)
    assert "<h1" in bare and "Пустой сбор" in bare and 'name="campaign" value="bare"' in bare
    for absent in (
        "camp-facts",
        "camp-goal",
        "camp-numbers",
        "camp-day",
        "camp-speakers",
        "camp-faq",
        "camp-panel-note",
    ):
        assert absent not in bare
    assert "<b>0 ₽</b>" in bare and "progressbar" not in bare
    assert re.findall(r'name="preset" value="(\d+)"( checked)?', bare) == [
        ("100000", ""),
        ("300000", " checked"),
        ("500000", ""),
        ("1000000", ""),
    ]
    missing = await browser_client.get("/give/unknown")
    assert missing.status_code == 404 and "Страница не найдена" in missing.text
    assert (await browser_client.get("/give/offer")).status_code == 200


async def test_plain_give_form_has_no_campaign_fields(browser_client):
    page = (await browser_client.get("/give")).text
    assert 'name="consent_gift"' not in page and 'name="campaign"' not in page and "camp-" not in page
    assert '<aside class="give-panel" aria-labelledby="give-form-title">' in page


async def test_editor_sees_campaigns_list_with_collected_amount(browser_client):
    listing = plain((await browser_client.get("/views/admin/campaigns", headers=EDITOR)).text)
    assert '<a href="/admin/campaigns" aria-current="page">' in listing and "<span>Сборы</span>" in listing
    assert '<h2><a href="/admin/campaigns?id=camp-1">Искусство влияния</a></h2>' in listing
    assert "<p>Собрано 52 000 ₽ из 1 500 000 ₽ · 3%</p>" in listing
    assert '<a href="/give/intensive" target="_blank" rel="noopener noreferrer">На сайте ↗</a>' in listing
    assert "Адрес /give/intensive · приглашение от 15 000 ₽" in listing
    assert 'href="/admin/campaigns?id=new">' in listing and "Нужен доступ администратора" not in listing


async def test_campaign_editor_pages_for_existing_new_and_missing_campaign(browser_client):
    existing = plain((await browser_client.get("/views/admin/campaigns?id=camp-1", headers=EDITOR)).text)
    assert 'data-api="/admin/campaigns/camp-1" data-method="PATCH"' in existing
    assert '<a href="/admin/campaigns">Сборы</a>' in existing and "office-page-header--record" in existing
    blocks = ("main", "goal", "about", "program", "rules", "letters", "publish")
    assert [f'id="campaign-{block}"' in existing for block in blocks] == [True] * len(blocks)
    assert '<input name="slug" type="text" value="intensive">' in existing
    assert (
        'name="goal_amount" type="number" min="0" step="0.01" inputmode="decimal" data-kopecks value="1500000"'
        in existing
    )
    assert "data-kopecks-lines>5000\n15000\n30000.50</textarea>" in existing
    assert (
        '<fieldset class="office-modules" data-modules="modules" data-modules-count="Блоков" '
        'data-modules-new="Новый блок" data-modules-required="Укажите название каждого блока."><legend>'
    ) in existing
    assert (
        '<input data-module-field="time" value="10:00-11:30" data-module-meta placeholder="Например, 10:00-11:30">'
        in existing
    )
    assert '<textarea data-module-field="points" data-module-lines>Цель\nФинал</textarea>' in existing
    assert '<textarea data-module-field="answer">Нет.</textarea>' in existing
    assert 'data-modules="faq" data-modules-count="Вопросов"' in existing and "data-modules-hours" not in existing
    assert "<strong>52 000 ₽</strong> из 1 500 000 ₽ · 3%" in existing
    assert 'data-method="DELETE" data-next="/admin/campaigns"' in existing
    assert 'class="program-editor__site" href="/give/intensive" target="_blank"' in existing
    assert '<option value="published" selected>Опубликовано</option>' in existing
    created = (await browser_client.get("/views/admin/campaigns?id=new", headers=EDITOR)).text
    assert 'data-method="POST" data-open-record="/admin/campaigns?id="' in created
    assert ">Создать сбор</button>" in created and "Удаление сбора" not in created and "Собрано" not in created
    assert "program-editor__site" not in created
    assert created.index('<option value="draft" >Черновик</option>') < created.index('<option value="published" >')
    missing = (await browser_client.get("/views/admin/campaigns?id=unknown", headers=EDITOR)).text
    assert "Сбор не найден: возможно, его удалили." in missing and "data-program-editor" not in missing


async def test_campaign_editor_lists_donors_for_invitation(browser_client):
    page = plain((await browser_client.get("/views/admin/campaigns?id=camp-1", headers=EDITOR)).text)
    block = page.split('id="campaign-invitations"', 1)[1].split("</section>", 1)[0]
    assert "Дарители с оплаченным пожертвованием в этот сбор от 15 000 ₽." in block
    assert "<h3>Вымышленный Даритель · 15 000 ₽</h3>" in block and "<h3>Без имени · 50 000 ₽</h3>" in block
    assert block.count(">Отправить приглашение</button>") == 1 and "disabled" not in block
    assert (
        '<button data-api="/admin/campaigns/camp-1/donors/DON-2026-000011/invite" '
        'data-confirm="Отправить приглашение на donor@example.test? Письмо уйдет один раз.">'
    ) in block
    assert "DON-2026-000010 · second@example.test · оплачено 4 ноября" in block
    assert "приглашение отправлено 6 ноября" in block and block.count('data-status="succeeded"') == 1
    assert "DON-2026-000009 · почта удалена по сроку хранения" in block
    created = (await browser_client.get("/views/admin/campaigns?id=new", headers=EDITOR)).text
    assert "campaign-invitations" not in created


async def campaign_editor_with_donors(donors):
    async def upstream(request):
        replies = {
            "/auth/admin-session": {"role": "editor"},
            "/admin/campaigns": [CAMPAIGN],
            "/admin/campaigns/camp-1/donors": donors,
        }
        return httpx.Response(200, json=replies.get(request.url.path, []))

    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://api.test") as api:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(api)), base_url="http://web.test"
        ) as client:
            return plain((await client.get("/views/admin/campaigns?id=camp-1", headers=EDITOR)).text)


async def test_campaign_invitations_wait_for_letter_text():
    page = await campaign_editor_with_donors({**CAMPAIGN_DONORS, "letter_ready": False})
    assert "Заполните тему и текст письма-приглашения и сохраните сбор: до этого кнопки неактивны." in page
    assert 'Письмо уйдет один раз." disabled>Отправить приглашение</button>' in page


@pytest.mark.parametrize("donors", [[], {}, {"threshold": 0, "letter_ready": True, "items": []}])
async def test_campaign_invitations_without_donors(donors):
    page = await campaign_editor_with_donors(donors)
    block = page.split('id="campaign-invitations"', 1)[1].split("</section>", 1)[0]
    assert "Дарителей для приглашения пока нет." in block and "Отправить приглашение" not in block
    assert "Дарители с оплаченным пожертвованием в этот сбор." in block


async def test_program_modules_editor_keeps_hours_total(browser_client):
    page = (await browser_client.get("/views/admin/portfolio?id=club-1", headers=ADMIN)).text
    assert (
        '<fieldset class="office-modules" data-modules="modules" data-modules-count="Модулей" '
        'data-modules-new="Новый модуль" data-modules-required="Укажите тему каждого модуля." data-modules-hours>'
    ) in page
    assert '<input data-module-field="hours" value="" data-module-meta placeholder="Например, 2 ак. ч.">' in page
    assert ">Добавить модуль</button>" in page and ">Удалить модуль</button>" in page


async def test_name_fields_hint_allowed_characters(browser_client):
    limit = resource("domain-data.json")["limits"]["person_name_max"]
    join = (await browser_client.get("/join")).text
    give = (await browser_client.get("/give")).text
    for page, name, required in ((join, "fio", True), (give, "name", False)):
        field = re.search(rf'<input name="{name}" type="text"[^>]*>', page)[0]
        assert f'maxlength="{limit}"' in field and 'minlength="2"' in field and 'autocomplete="name"' in field
        assert 'pattern="[\\p{L}\\-\'’. ]+"' in field
        assert ("required" in field) is required


async def test_verified_profile_shows_name_read_only_with_support_link():
    alumni = {**PROFILE["alumni"], "verification_status": "verified", "fio": "Анна Вымышленная"}
    page = await profile_page({**PROFILE, "alumni": alumni})
    field = re.search(r'<input type="text" value="Анна Вымышленная"[^>]*>', page)[0]
    assert "readonly" in field and "name=" not in field
    assert '<a href="/support">напишите в поддержку</a>' in page
