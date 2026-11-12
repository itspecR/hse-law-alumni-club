import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from club_web import features
from club_web.calendar import google_calendar, instant, month_view
from club_web.catalog import UPCOMING_LIMIT, club_programs_preview, prepare_catalog
from club_web.changes import parse_changes, select_changes
from club_web.client import ApiFailure
from club_web.formatting import initials, lowest_price, page_items, rub, safe_url
from club_web.office import (
    CHOICES,
    CREATE_DEFAULTS,
    DESCRIPTIONS,
    FIELDS,
    GROUPS,
    LABELS,
    METRICS,
    READ_ONLY_SECTIONS,
    SECTION_FILTERS,
    SECTION_LABELS,
    SINGLE_CHOICES,
    choice_options,
    choice_sources,
    cohort_options,
    filter_content,
    portfolio_blocks,
    section_records,
    status_values,
    visible_groups,
)
from club_web.pricing import discount_tiers, member_prices
from club_web.search import search_groups

HOME_EPISODES = 4
HOME_CHANGES = 3
ACHIEVEMENT_STRIP = 4
FULL_ADMIN_ROLES = ("admin", "Administrator")
PAY_PREFIX = "/pay/"
PAYMENT_FAILURES = {404: ("missing", 404), 410: ("closed", 410)}
PAYMENT_UNAVAILABLE = ("unavailable", 503)
POINT_PROGRAM_GROUPS = (("portfolio", "Программы клуба"), ("dpo", "Программы ДПО"))
FULL_PROGRESS = 100
GIVE_AMOUNTS = (100000, 300000, 500000, 1000000)
GIVE_CHOSEN = 300000
HEADER_ROOMY_LINKS = 6
FORBIDDEN = 403
NEW_RECORD = "new"
PROGRAM_EDITOR_LEDE = (
    "Карточка программы показывает, как она будет выглядеть на сайте. Изменения появятся на сайте после сохранения."
)
PUBLIC_PAGES = {
    "/": ("home.html", "Клуб выпускников факультета права"),
    "/tg": ("mini.html", "Клуб выпускников"),
    "/dpo": ("catalog.html", "Программы ДПО"),
    "/portfolio": ("portfolio.html", "Портфель программ"),
    "/merch": ("catalog.html", "Мерч клуба"),
    "/events": ("events.html", "События клуба"),
    "/news": ("news.html", "Новости"),
    "/podcasts": ("podcasts.html", "Подкасты клуба"),
    "/changes": ("changes.html", "Изменения в праве"),
    "/saved": ("saved.html", "Сохраненное"),
    "/search": ("search.html", "Поиск по сайту"),
    "/join": ("auth.html", "Вступить в клуб"),
    "/forgot": ("auth.html", "Восстановить пароль"),
    "/reset": ("auth.html", "Новый пароль"),
    "/confirm": ("auth.html", "Подтвердить почту"),
    "/privacy": ("legal/privacy.html", "Политика обработки персональных данных"),
    "/terms": ("legal/terms.html", "Пользовательское соглашение"),
    "/consent": ("legal/consent.html", "Согласие на обработку персональных данных"),
    "/mailing-consent": ("legal/mailing-consent.html", "Согласие на получение рассылок"),
    "/confidential": ("legal/confidential.html", "Политика конфиденциальности"),
    "/requisites": ("legal/requisites.html", "Реквизиты"),
    "/give": ("give.html", "Поддержать факультет"),
    "/give/thanks": ("give-thanks.html", "Спасибо за поддержку"),
    "/give/offer": ("legal/pending.html", "Оферта о пожертвовании"),
    "/give/consent": ("legal/pending.html", "Согласие дарителя на обработку персональных данных"),
    "/support": ("support.html", "Поддержка клуба"),
    "/support/consent": ("legal/support.html", "Согласие на обработку данных"),
    "/portfolio-consent": ("legal/portfolio-consent.html", "Согласие на обработку персональных данных"),
    "/portfolio-terms": ("legal/portfolio-terms.html", "Условия обучения"),
    "/cart": ("cart.html", "Корзина"),
    "/lk": ("cabinet.html", "Личный кабинет"),
    "/lk/profile": ("profile.html", "Профиль"),
}
NAV = [
    ("/portfolio", "Портфель программ"),
    ("/dpo", "ДПО"),
    ("/events", "События"),
    ("/news", "Новости"),
    ("/podcasts", "Подкасты"),
    ("/changes", "Изменения в праве"),
    ("/merch", "Мерч"),
]
LIVE_VIEWS = (
    ("/admin", "office"),
    ("/lk", "member"),
    ("/podcasts", "member"),
    ("/events", "member"),
    ("/cart", "cart"),
)
VEILED_VIEWS = ("/admin", "/lk", "/cart")
OFFICE_NAV = [
    ("overview", "Обзор"),
    ("analytics", "Аналитика"),
    ("orders", "Заявки"),
    ("donations", "Пожертвования"),
    ("promo-codes", "Промокоды"),
    ("members", "Выпускники"),
    ("subs", "Подписки"),
    ("portfolio", "Портфель программ"),
    ("cohorts", "Потоки"),
    ("trainers", "Тренеры"),
    ("programs", "ДПО"),
    ("products", "Мерч"),
    ("news", "Новости"),
    ("news-sources", "Источники новостей"),
    ("events", "События"),
    ("podcasts", "Подкасты"),
    ("pages", "Страницы"),
    ("media", "Медиа"),
    ("audit", "Журнал"),
    ("support", "Поддержка"),
]
OFFICE_ENDPOINTS = {
    "overview": "/admin/overview",
    "analytics": "/admin/analytics",
    "orders": "/admin/orders",
    "donations": "/admin/donations",
    "promo-codes": "/admin/promo-codes",
    "members": "/admin/members",
    "subs": "/admin/podcast-subs",
    "portfolio": "/admin/portfolio",
    "cohorts": "/admin/cohorts",
    "trainers": "/admin/trainers",
    "programs": "/admin/programs",
    "products": "/admin/products",
    "news": "/admin/news",
    "news-sources": "/admin/news-sources",
    "events": "/admin/events",
    "podcasts": "/admin/podcasts",
    "media": "/admin/media",
    "audit": "/admin/audit",
    "support": "/admin/support",
}


def resource(name):
    path = Path(__file__).parent / "data" / name
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "data" / name
    return json.loads(path.read_text(encoding="utf-8"))


def select_page(path):
    if path in PUBLIC_PAGES:
        return PUBLIC_PAGES[path]
    if path.startswith("/admin") and (path == "/admin" or path.removeprefix("/admin/") in dict(OFFICE_NAV)):
        section = path.removeprefix("/admin/") if path != "/admin" else "overview"
        return "office.html", dict(OFFICE_NAV)[section]
    for prefix, template in (
        ("/dpo/", "not-found.html"),
        ("/portfolio/", "portfolio-program.html"),
        ("/merch/", "product.html"),
        ("/news/", "article.html"),
        ("/events/", "events.html"),
        ("/podcasts/", "episode.html"),
        ("/changes/", "changes.html"),
        (PAY_PREFIX, "pay.html"),
    ):
        if path.startswith(prefix) and "/" not in path[len(prefix) :] and path[len(prefix) :]:
            return template, "Клуб выпускников"
    return "not-found.html", "Страница не найдена"


def point_program_groups(rows):
    return [(label, [row for row in rows if row.get("catalog") == catalog]) for catalog, label in POINT_PROGRAM_GROUPS]


def with_member_prices(program, levels):
    rows = member_prices(program["price"], levels)
    return {**program, "member_prices": rows, "member_price_map": {row["level"]: rub(row["price"]) for row in rows}}


def with_member_from(program, levels):
    price = lowest_price(program)
    if not price:
        return program
    return {**program, "member_from": rub(min(row["price"] for row in member_prices(price, levels)))}


async def program_payment(api, token):
    try:
        return {"payment": await api.get("/transfers/" + quote(token, safe=""))}
    except ApiFailure as failure:
        state, status = PAYMENT_FAILURES.get(failure.status, PAYMENT_UNAVAILABLE)
        return {"payment": None, "payment_state": state, "status": status}


def program_redirect(item):
    url = safe_url((item or {}).get("source_url"))
    return url if url.startswith("https://") else ""


def home_programs(programs, levels, today=None):
    catalog = prepare_catalog([item for item in programs if item.get("price")], {}, today)
    starts = catalog["upcoming"]
    shown = starts or [item for item in catalog["items"] if item.get("enrollment") == "actual"][:UPCOMING_LIMIT]
    return {
        "levels": discount_tiers(levels),
        "home_starts": bool(starts),
        "home_programs": [with_member_prices(item, levels) for item in shown],
        "programs_total": len(programs),
        "programs_open": sum(item.get("enrollment") == "actual" for item in programs),
    }


def achievement_share(item):
    return (item.get("current") or 0) / (item.get("target") or 1)


def ordered_achievements(items):
    return sorted(items, key=lambda item: (not item.get("earned"), -achievement_share(item)))


def with_achievements(data):
    achievements = ordered_achievements(data["me"].get("achievements") or [])
    data["achievements"] = achievements
    data["achievement_strip"] = achievements[:ACHIEVEMENT_STRIP]
    data["achievements_earned"] = sum(bool(item.get("earned")) for item in achievements)


def level_progress(level, levels):
    current = next((item for item in levels if item["key"] == level.get("level")), None)
    following = next((item for item in levels if item["title"] == level.get("next_level")), None)
    if not current or not following:
        return FULL_PROGRESS
    span = following["min_points"] - current["min_points"]
    return round(FULL_PROGRESS * (span - (level.get("to_next") or 0)) / span) if span > 0 else FULL_PROGRESS


def activity_chart(months):
    peak = max((month.get("points") or 0 for month in months), default=0)
    if peak <= 0:
        return []
    return [{**month, "share": round(FULL_PROGRESS * max(month.get("points") or 0, 0) / peak)} for month in months]


def with_cabinet(data):
    with_achievements(data)
    me = data["me"]
    data["level_progress"] = level_progress(me.get("level") or {}, data["domain"]["levels"])
    data["activity_chart"] = activity_chart(me.get("activity") or [])


def pagination_page(value):
    try:
        return max(1, min(10000, int(value)))
    except TypeError, ValueError:
        return 1


def live_view(path):
    kind = next((kind for prefix, kind in LIVE_VIEWS if path.startswith(prefix)), "")
    return {"kind": kind, "veiled": path.startswith(VEILED_VIEWS)}


def recent_changes():
    snapshot = parse_changes(resource("changes.json"))
    return select_changes(snapshot["items"], {"view": "digest" if snapshot["mode"] == "channel" else "all"})[
        :HOME_CHANGES
    ]


def portfolio_editor(data, record_id):
    if not record_id:
        data["office_template"] = "office/portfolio.html"
        return
    program = {} if record_id == NEW_RECORD else next((row for row in data["rows"] if row.get("id") == record_id), None)
    data.update(
        office_template="office/portfolio-editor.html",
        office_parent=dict(OFFICE_NAV)["portfolio"],
        office_lede=PROGRAM_EDITOR_LEDE,
        office_blocks=portfolio_blocks(),
        program=program,
        title=(program or {}).get("title") or ("Новая программа" if program == {} else "Программа не найдена"),
    )


def office_denied(path, status):
    return status == FORBIDDEN and path.startswith("/admin")


def navigation():
    return [(href, label) for href, label in NAV if features.section_enabled(href)]


def changes_items():
    return parse_changes(resource("changes.json"))["items"] if features.CHANGES_ENABLED else None


class Page:
    def __init__(self, api, path, params, authorized, template, data):
        self.api, self.path, self.params, self.authorized = api, path, params, authorized
        self.template, self.data = template, data

    async def load(self, key, endpoint, default=None):
        try:
            self.data[key] = await self.api.get(endpoint)
        except ApiFailure as error:
            self.data[key] = default
            self.data["load_errors"][key] = error.status
            if not office_denied(self.path, error.status):
                self.data["errors"].append(error.message)
            if error.status in (401, 403):
                self.data["access_error"] = error.status
            if key == "item" and error.status >= 500:
                self.data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
            if key == "item" and error.status == 404:
                self.data.update(template="not-found.html", title="Страница не найдена", status=404)
        return self.data[key]


async def home_page(page):
    data, load = page.data, page.load
    await asyncio.gather(
        load("page", "/pages/home", {}),
        load("news", "/news?limit=3", []),
        load("programs", "/programs", []),
        load("portfolio", "/portfolio", []),
        load("events", "/events", []),
        load("podcasts", "/podcasts", {"items": []}),
    )
    data["hero"] = (data["page"].get("blocks") or {}).get("hero") or {}
    data["cta"] = (data["page"].get("blocks") or {}).get("cta") or {}
    now = datetime.now(UTC)
    data["upcoming"] = sorted(
        [item for item in data["events"] if instant(item.get("starts_at")) >= now],
        key=lambda item: instant(item["starts_at"]),
    )[:2]
    data.update(home_programs(data["programs"], data["domain"]["levels"]))
    data["club_programs"] = club_programs_preview(page_items(data["portfolio"]))
    data["episodes"] = page_items(data["podcasts"])[:HOME_EPISODES]
    data["recent_changes"] = recent_changes() if features.CHANGES_ENABLED else []


async def catalog_page(page):
    data, load, params, path, authorized = page.data, page.load, page.params, page.path, page.authorized
    kind = "dpo" if path == "/dpo" else "merch"
    await load("items", "/programs" if kind == "dpo" else "/products", [])
    data["kind"] = kind
    if kind == "dpo":
        data.update(prepare_catalog(data["items"], params))
        data["items"] = [with_member_from(item, data["domain"]["levels"]) for item in data["items"]]
    else:
        fields = ("category",)
        data["filters"] = {
            field: sorted({str(item.get(field) or "") for item in data["items"]} - {""}) for field in fields
        }
        data["items"] = [
            item
            for item in data["items"]
            if all(not params.get(field) or item.get(field) == params[field] for field in fields)
            and params.get("q", "").casefold() in item.get("title", "").casefold()
        ]
        if params.get("sort", "title") == "title":
            data["items"].sort(key=lambda item: item.get("title", "").casefold())
        elif params.get("sort") == "price":
            data["items"].sort(key=lambda item: item.get("price") or 0)
        elif params.get("sort") == "price-desc":
            data["items"].sort(key=lambda item: -(item.get("price") or 0))
    if authorized:
        await load("me", "/me", {})


async def portfolio_list_page(page):
    load = page.load
    await load("items", "/portfolio", [])


async def portfolio_item_page(page):
    data, load, path = page.data, page.load, page.path
    await load("item", "/portfolio/" + quote(path.rsplit("/", 1)[1], safe=""), {})
    if data["item"]:
        data["title"] = data["item"]["title"]


async def dpo_item_page(page):
    data, load, path = page.data, page.load, page.path
    await load("item", "/programs/" + quote(path.rsplit("/", 1)[1], safe=""), {})
    data["redirect"] = program_redirect(data["item"])


async def payment_page(page):
    data, path, api = page.data, page.path, page.api
    data.update(await program_payment(api, path.removeprefix(PAY_PREFIX)), title="Оплата заявки")


async def news_item_page(page):
    data, load, path = page.data, page.load, page.path
    await load("item", "/news/" + quote(path.rsplit("/", 1)[1], safe=""), {})
    if data["item"]:
        data["title"] = data["item"]["title"]


async def merch_item_page(page):
    data, load, path = page.data, page.load, page.path
    await load("products", "/products", [])
    data["item"] = next((item for item in data["products"] if item["slug"] == path.rsplit("/", 1)[1]), None)
    if data["item"]:
        data["title"] = data["item"]["title"]
    elif "products" in data["load_errors"]:
        data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
    else:
        data.update(template="not-found.html", status=404)


async def search_page(page):
    data, load, params = page.data, page.load, page.params
    await asyncio.gather(
        load("portfolio_programs", "/portfolio", []),
        load("programs", "/programs", []),
        load("news", "/news", []),
        load("events", "/events", []),
        load("podcasts", "/podcasts", {"items": []}),
    )
    needle = params.get("q", "").casefold().replace("ё", "е").strip()
    groups = search_groups(
        page_items(data["portfolio_programs"]),
        data["programs"],
        data["news"],
        data["events"],
        page_items(data["podcasts"]),
        changes_items(),
        params.get("q", ""),
    )
    data["groups"] = groups
    data["sections"] = [
        (href, label)
        for href, label in navigation() + [("/lk", "Личный кабинет"), ("/support", "Поддержка")]
        if not needle or needle in label.casefold().replace("ё", "е")
    ]
    data["found"] = sum(len(group["items"]) for group in groups) + len(data["sections"])


async def news_list_page(page):
    load = page.load
    await load("items", "/news", [])


async def events_page(page):
    data, load, params, path = page.data, page.load, page.params, page.path
    event_id = path.removeprefix("/events/") if path != "/events" else params.get("event")
    endpoint = "/events"
    if event_id:
        endpoint += "?id=" + quote(event_id, safe="")
    elif params.get("month"):
        endpoint += "?month=" + month_view([], params["month"])["selected"]
    await load("items", endpoint, [])
    for event in data["items"]:
        event["calendar_url"] = google_calendar(event)
        event["past"] = event.get("status") in ("done", "canceled") or instant(event.get("starts_at")) < datetime.now(
            UTC
        )
    data["calendar"] = month_view(data["items"], params.get("month"))
    data["item"] = next((item for item in data["items"] if item["id"] == event_id), None)
    if event_id and "items" in data["load_errors"]:
        data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
    elif event_id and not data["item"]:
        data.update(template="not-found.html", status=404)


async def podcasts_page(page):
    data, load, path = page.data, page.load, page.path
    await load("podcasts", "/podcasts", {"items": []})
    data["items"] = page_items(data["podcasts"])
    if path != "/podcasts":
        data["item"] = next((item for item in data["items"] if item["id"] == path.rsplit("/", 1)[1]), None)
        if data["item"]:
            data["title"] = data["item"]["title"]
        elif "podcasts" in data["load_errors"]:
            data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
        else:
            data.update(template="not-found.html", status=404)


async def changes_soon_page(page):
    data = page.data
    data["template"] = "changes-soon.html"


async def changes_page(page):
    data, params, path = page.data, page.params, page.path
    snapshot = parse_changes(resource("changes.json"))
    data["snapshot"] = snapshot
    data["item"] = (
        next((item for item in snapshot["items"] if item["id"] == path.rsplit("/", 1)[1]), None)
        if path != "/changes"
        else None
    )
    if data["item"]:
        data["title"] = data["item"]["title"]
    if path != "/changes" and not data["item"]:
        data.update(template="not-found.html", status=404)
    data["items"] = select_changes(snapshot["items"], {"view": "digest", **params})
    data["total"] = len(data["items"])
    page = pagination_page(params.get("page", "1"))
    data["items"] = data["items"][(page - 1) * 20 : page * 20]
    data["page_number"] = page
    retained = {
        key: value
        for key, value in params.items()
        if key in ("q", "view", "kind", "topic", "from", "to", "sort", "page")
    }
    suffix = "?" + urlencode(retained) if retained else ""
    data["changes_suffix"] = suffix
    data["changes_back"] = "/changes" + suffix
    data["previous_page"] = "/changes?" + urlencode({**retained, "page": page - 1}) if page > 1 else ""
    data["next_page"] = "/changes?" + urlencode({**params, "page": page + 1}) if data["total"] > page * 20 else ""
    data["filters"] = {
        key: sorted({item.get(key) or "" for item in snapshot["items"]} - {""}) for key in ("kind", "topic")
    }


async def cabinet_page(page):
    data, load, path = page.data, page.load, page.path
    await load("me", "/me", {})
    data["initials"] = initials(data["me"].get("alumni", {}).get("fio"))
    if path == "/lk" and data["me"].get("alumni", {}).get("verification_status") == "verified":
        await asyncio.gather(
            load("orders", "/me/orders", []),
            load("ledger", "/me/ledger", []),
            load("classmates", "/me/classmates", []),
            load("notifications", "/me/events", []),
            load("club_events", "/events", []),
            load("club_news", "/news?limit=3", []),
        )
        now = datetime.now(UTC)
        upcoming = sorted(
            [item for item in data["club_events"] if instant(item.get("starts_at")) >= now],
            key=lambda item: instant(item["starts_at"]),
        )
        data["next_event"] = upcoming[0] if upcoming else None
        with_cabinet(data)


async def cart_page(page):
    load = page.load
    await load("cart", "/cart", {"items": [], "subtotal": 0})


async def support_page(page):
    data, load = page.data, page.load
    await load("support", "/support/config", {"enabled": False})
    data["faq"] = resource("faq-data.json")


async def give_page(page):
    data, load = page.data, page.load
    await load("donation", "/donations/config", {"enabled": False, "card": False, "transfer": False})
    data.update(give_amounts=GIVE_AMOUNTS, give_chosen=GIVE_CHOSEN)


OFFICE_QUERY_KEYS = ("q", "status", "payment", "page", "range", "campaign")


def office_pagination(data, params, section):
    records = data.get("records") or {}
    current = pagination_page(params.get("page", "1"))
    limit = (records.get("limit") or records.get("page_size") or 30) if isinstance(records, dict) else 30
    total = records.get("total", 0) if isinstance(records, dict) else 0
    data["office_previous"] = "?" + urlencode({**params, "page": current - 1}) if current > 1 else ""
    data["office_next"] = (
        "?" + urlencode({**params, "page": current + 1})
        if total > current * limit or section == "support" and len(data["rows"]) == 30
        else ""
    )


async def office_page(page):
    data, path = page.data, page.path
    section = path.removeprefix("/admin/") if path != "/admin" else "overview"
    data["section"] = section
    data["labels"] = {**LABELS, **SECTION_LABELS.get(section, {})}
    data["office_template"] = "office/" + ("content" if section in FIELDS else section) + ".html"
    data["office_statuses"] = status_values(section)
    data["office_filters"] = SECTION_FILTERS.get(section, ())
    if not page.authorized or page.template == "not-found.html":
        return
    await page.load("session", "/auth/admin-session", {})
    if not data["session"]:
        return
    data["role"] = data["session"]["role"]
    data["full_admin"] = data["role"] in FULL_ADMIN_ROLES
    data["office_groups"] = visible_groups(data["full_admin"])
    await office_records(page, section)
    await office_extras(page, section)
    office_rows(data, page.params, section)


async def office_records(page, section):
    data, load, params = page.data, page.load, page.params
    if section == "support":
        await load("bot", "/admin/bot-status", {})
    if section == "support" and data["role"] not in FULL_ADMIN_ROLES:
        data["records"] = []
    elif section == "pages":
        await load("record", "/admin/pages/home", {})
    else:
        query = urlencode({key: params[key] for key in OFFICE_QUERY_KEYS if key in params})
        await load("records", OFFICE_ENDPOINTS[section] + ("?" + query if query else ""), [])


async def office_extras(page, section):
    data, load, params = page.data, page.load, page.params
    full_admin = data["role"] in FULL_ADMIN_ROLES
    if section == "overview":
        await load("health", "/admin/system-health", {})
    if section == "programs":
        await load("dpo_sync", "/admin/dpo-sync", {})
    if section == "members" and full_admin:
        data["point_programs"] = point_program_groups(
            page_items(await load("point_programs", "/admin/point-programs", []))
        )
    for source in choice_sources(section):
        rows = page_items(await load("choices:" + source, OFFICE_ENDPOINTS[source], []))
        data["office_options"][source] = choice_options(source, rows)
        data["office_names"][source] = {row["id"]: row["label"] for row in data["office_options"][source]}
    if section == "orders":
        data["office_cohorts"] = cohort_options(page_items(await load("cohorts", "/admin/cohorts", [])))
    if params.get("id") and section == "events":
        await load("roster", "/admin/events/" + quote(params["id"], safe="") + "/rsvps", [])
    if params.get("id") and section == "members" and full_admin:
        await load("point_history", "/admin/members/" + quote(params["id"], safe="") + "/points", {})


def office_rows(data, params, section):
    data["rows"] = section_records(section, page_items(data.get("records")), data["office_names"])
    if section == "portfolio":
        portfolio_editor(data, params.get("id"))
    if section in FIELDS:
        data["office_total"] = len(data["rows"])
        data["rows"] = filter_content(data["rows"], params)
    office_pagination(data, params, section)


PAGE_ROUTES = (
    (lambda page: page.path == "/", home_page),
    (lambda page: page.path in ("/dpo", "/merch"), catalog_page),
    (lambda page: page.path == "/portfolio", portfolio_list_page),
    (lambda page: page.path.startswith("/portfolio/"), portfolio_item_page),
    (lambda page: page.path.startswith("/dpo/"), dpo_item_page),
    (lambda page: page.path.startswith(PAY_PREFIX) and page.template == "pay.html", payment_page),
    (lambda page: page.path.startswith("/news/"), news_item_page),
    (lambda page: page.path.startswith("/merch/"), merch_item_page),
    (lambda page: page.path == "/search", search_page),
    (lambda page: page.path == "/news", news_list_page),
    (lambda page: page.path == "/events" or page.path.startswith("/events/"), events_page),
    (lambda page: page.path == "/podcasts" or page.path.startswith("/podcasts/"), podcasts_page),
    (lambda page: not features.section_enabled(page.path), changes_soon_page),
    (lambda page: page.path == "/changes" or page.path.startswith("/changes/"), changes_page),
    (lambda page: page.path in ("/lk", "/lk/profile") and page.authorized, cabinet_page),
    (lambda page: page.path == "/cart" and "x-cart-session" in page.api.headers, cart_page),
    (lambda page: page.path == "/support", support_page),
    (lambda page: page.path == "/give", give_page),
    (lambda page: page.path.startswith("/admin"), office_page),
)


async def context(api, path, params, authorized=False):
    template, title = select_page(path)
    nav = navigation()
    data = {
        "template": template,
        "title": title,
        "path": path,
        "query": params,
        "nav": nav,
        "nav_dense": len(nav) > HEADER_ROOMY_LINKS,
        "changes_enabled": features.CHANGES_ENABLED,
        "live": live_view(path),
        "office_nav": OFFICE_NAV,
        "office_groups": GROUPS,
        "office_descriptions": DESCRIPTIONS,
        "office_fields": FIELDS,
        "office_choices": CHOICES,
        "office_single": SINGLE_CHOICES,
        "office_cohorts": {},
        "office_defaults": CREATE_DEFAULTS,
        "office_read_only": READ_ONLY_SECTIONS,
        "office_options": {},
        "office_names": {},
        "labels": LABELS,
        "metrics": METRICS,
        "errors": [],
        "load_errors": {},
        "authorized": authorized,
        "status": 404 if template == "not-found.html" else 200,
        "domain": resource("domain-data.json"),
        "legal": resource("legal-data.json"),
    }
    data["reason_labels"] = {
        **{rule["reason"]: rule["description"] for rule in data["domain"]["point_rules"]},
        "manual": "Начисление клуба",
        "decay": "Списание за неактивность",
        "event_undo": "Отметка участия снята",
    }
    data["point_values"] = {rule["reason"]: rule["points"] for rule in data["domain"]["point_rules"]}

    page = Page(api, path, params, authorized, template, data)
    handler = next((handler for matches, handler in PAGE_ROUTES if matches(page)), None)
    if handler:
        await handler(page)
    return data
