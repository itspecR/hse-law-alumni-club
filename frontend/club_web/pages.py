import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from club_web import features
from club_web.calendar import google_calendar, instant, month_view
from club_web.campaigns import WAVE, campaign_facts, form_amounts, people_groups
from club_web.catalog import UPCOMING_LIMIT, club_programs_preview, prepare_catalog
from club_web.changes import parse_changes, select_changes
from club_web.client import ApiFailure
from club_web.formatting import address, initials, lowest_price, page_items, rub, safe_url
from club_web.office import (
    CHOICES,
    CREATE_DEFAULTS,
    DESCRIPTIONS,
    EDITORS,
    FIELDS,
    GROUPS,
    LABELS,
    METRICS,
    PEOPLE_CHOICES,
    READ_ONLY_SECTIONS,
    ROLE_OPTIONS,
    ROW_EDITORS,
    SECTION_FILTERS,
    SECTION_LABELS,
    SINGLE_CHOICES,
    audit_rows,
    choice_options,
    choice_sources,
    cohort_options,
    editor_blocks,
    filter_content,
    record_copy,
    section_records,
    status_values,
    visible_groups,
)
from club_web.pricing import discount_tiers, member_prices
from club_web.registration import registration_open
from club_web.schema import TELEGRAM_CHANNEL
from club_web.search import search_groups
from club_web.tabs import mobile_tabs

HOME_EPISODES = 4
PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
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
SERVER_ERROR = 500
SESSION_KEYS = frozenset(("me", "session"))
NEW_RECORD = "new"
PUBLIC_PAGES = {
    "/": ("home.html", "Клуб выпускников факультета права"),
    "/tg": ("mini.html", "Клуб выпускников"),
    "/dpo": ("catalog.html", "Программы ДПО"),
    "/portfolio": ("portfolio.html", "Тренинги по развитию навыков"),
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
    "/terms": ("legal/terms.html", "Пользовательское соглашение"),
    "/consent": ("legal/consent.html", "Согласие на обработку персональных данных"),
    "/documents": ("legal/documents.html", "Документы"),
    "/about": ("about.html", "О клубе"),
    "/requisites": ("legal/requisites.html", "Реквизиты"),
    "/give": ("give.html", "Поддержать факультет"),
    "/give/thanks": ("give-thanks.html", "Спасибо за поддержку"),
    "/support": ("support.html", "Поддержка клуба"),
    "/support/consent": ("legal/support.html", "Согласие на обработку данных"),
    "/cart": ("cart.html", "Корзина"),
    "/lk": ("cabinet.html", "Личный кабинет"),
    "/lk/profile": ("profile.html", "Профиль"),
}
NAV = [
    ("/portfolio", "Тренинги"),
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
    ("campaigns", "Сборы"),
    ("promo-codes", "Промокоды"),
    ("members", "Выпускники"),
    ("subs", "Подписки"),
    ("portfolio", "Портфель программ"),
    ("cohorts", "Потоки"),
    ("trainers", "Спикеры и тренеры"),
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
    "campaigns": "/admin/campaigns",
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


LEGAL_FILES = {
    path: document["file"]
    for document in resource("legal-data.json")["documents"].values()
    for path in document["paths"]
}


def select_page(path):
    if path in PUBLIC_PAGES:
        return PUBLIC_PAGES[path]
    if path.startswith("/admin") and (path == "/admin" or path.removeprefix("/admin/") in dict(OFFICE_NAV)):
        section = path.removeprefix("/admin/") if path != "/admin" else "overview"
        return "office.html", dict(OFFICE_NAV)[section]
    for prefix, template in (
        ("/dpo/", "not-found.html"),
        ("/portfolio/", "portfolio-program.html"),
        ("/give/", "campaign.html"),
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


def record_templates(section):
    return resource("campaign-templates.json") if section == "campaigns" else {}


def new_record(section, params):
    template = record_templates(section).get(params.get("template", ""))
    return dict(template["record"]) if template else {}


def record_editor(data, section, params):
    editor, record_id = EDITORS[section], params.get("id")
    data["office_record_templates"] = {key: value["label"] for key, value in record_templates(section).items()}
    if not record_id:
        data["office_template"] = editor["list"]
        return
    record = (
        new_record(section, params)
        if record_id == NEW_RECORD
        else next((row for row in data["rows"] if row.get("id") == record_id), None)
    )
    data.update(
        office_template=editor["editor"],
        office_parent=dict(OFFICE_NAV)[section],
        office_lede=editor["lede"],
        office_blocks=editor_blocks(section),
        program=record,
        title=(record or {}).get("title") or (editor["missing"] if record is None else editor["new"]),
    )


def office_denied(path, status):
    return status == FORBIDDEN and path.startswith("/admin")


def navigation():
    return [(href, label) for href, label in NAV if features.link_shown(href)]


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
            if key in SESSION_KEYS and error.status >= SERVER_ERROR:
                self.data["session_unavailable"] = True
            elif not office_denied(self.path, error.status):
                self.data["errors"].append(error.message)
            if error.status in (401, 403):
                self.data["access_error"] = error.status
            if key == "item" and error.status >= 500:
                self.data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
            if key == "item" and error.status == 404:
                self.data.update(template="not-found.html", title="Страница не найдена", status=404)
        return self.data[key]


async def current_campaign(api):
    try:
        reply = await api.get("/campaigns/current")
    except ApiFailure:
        return None
    campaign = reply.get("campaign") if isinstance(reply, dict) else None
    return campaign if isinstance(campaign, dict) and campaign.get("slug") else None


async def page_texts(api, slug):
    try:
        reply = await api.get("/pages/" + slug)
    except ApiFailure:
        return {}
    return reply if isinstance(reply, dict) else {}


async def about_texts(api):
    saved, defaults = await page_texts(api, "about"), resource("about-data.json")
    return {name: saved.get(name) or text for name, text in defaults.items()}


def first_paragraph(text):
    return PARAGRAPH_BREAK.split(text.strip(), maxsplit=1)[0]


async def about_page(page):
    page.data["about"] = await about_texts(page.api)
    page.data["title"] = page.data["about"]["title"]


async def home_page(page):
    data, load = page.data, page.load
    *_, data["campaign"], about = await asyncio.gather(
        load("page", "/pages/home", {}),
        load("news", "/news?limit=3", []),
        load("programs", "/programs", []),
        load("portfolio", "/portfolio", []),
        load("events", "/events", []),
        load("podcasts", "/podcasts", {"items": []}),
        current_campaign(page.api),
        about_texts(page.api),
    )
    data["about_intro"] = first_paragraph(about["body"])
    data["campaign_wave"] = WAVE
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
        data["programs"] if features.DPO_ENABLED else [],
        data["news"],
        data["events"],
        page_items(data["podcasts"]),
        changes_items(),
        params.get("q", ""),
    )
    data["groups"] = groups
    data["sections"] = [
        (href, label)
        for href, label in navigation() + [("/lk", "Кабинет"), ("/support", "Поддержка")]
        if not needle or needle in label.casefold().replace("ё", "е")
    ]
    data["found"] = sum(len(group["items"]) for group in groups) + len(data["sections"])


async def news_list_page(page):
    load = page.load
    await load("items", "/news", [])


def found(items, key):
    return next((item for item in items if key and key in (item["id"], item.get("slug"))), None)


def moved(path, item):
    section, key = path.rsplit("/", 1)
    return "" if key == address(item) else section + "/" + quote(address(item), safe="")


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
    data["item"] = found(data["items"], event_id)
    if event_id and data["load_errors"].get("items", 0) >= SERVER_ERROR:
        data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
    elif event_id and not data["item"]:
        data.update(template="not-found.html", status=404)
    elif path != "/events":
        data["moved"] = moved(path, data["item"])


async def podcasts_page(page):
    data, load, path = page.data, page.load, page.path
    await load("podcasts", "/podcasts", {"items": []})
    data["items"] = page_items(data["podcasts"])
    if path != "/podcasts":
        data["item"] = found(data["items"], path.rsplit("/", 1)[1])
        if data["item"]:
            data["title"] = data["item"]["title"]
            data["moved"] = moved(path, data["item"])
        elif "podcasts" in data["load_errors"]:
            data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
        else:
            data.update(template="not-found.html", status=404)


async def closed_page(page):
    page.data["redirect"] = features.HOME_PATH


async def legal_file_page(page):
    page.data["redirect"] = LEGAL_FILES[page.path]


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
            load("payments", "/payments/config", {"enabled": False}),
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


def open_answers(answers):
    return [answer for answer in answers if features.link_shown(answer["anchor"])]


async def support_page(page):
    data, load = page.data, page.load
    await load("support", "/support/config", {"enabled": False})
    faq = resource("faq-data.json")
    data["faq"] = {**faq, "answers": open_answers(faq["answers"])}


async def give_page(page):
    data, load = page.data, page.load
    await load("donation", "/donations/config", {"enabled": False, "card": False, "transfer": False})
    data.update(give_amounts=GIVE_AMOUNTS, give_chosen=GIVE_CHOSEN)


async def campaign_page(page):
    data, load, path = page.data, page.load, page.path
    campaign = await load("item", "/campaigns/" + quote(path.rsplit("/", 1)[1], safe=""), {})
    if not campaign:
        return
    await load("donation", "/donations/config", {"enabled": False, "card": False, "transfer": False})
    amounts, chosen = form_amounts(campaign, GIVE_AMOUNTS, GIVE_CHOSEN)
    data.update(
        title=campaign["title"],
        give_campaign=campaign,
        give_amounts=amounts,
        give_chosen=chosen,
        campaign_wave=WAVE,
        campaign_facts=campaign_facts(campaign),
        campaign_people=people_groups(campaign.get("speakers") or []),
    )


OFFICE_QUERY_KEYS = ("q", "status", "payment", "page", "range", "campaign")


def office_pagination(data, params, section):
    records = data.get("records") or {}
    current = pagination_page(params.get("page", "1"))
    limit = (records.get("limit") or records.get("page_size") or 30) if isinstance(records, dict) else 30
    total = records.get("total", 0) if isinstance(records, dict) else 0
    data["office_records_total"] = total
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
    data["office_copy"] = record_copy(section)
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
        data["about_record"] = await page_texts(page.api, "about")
        data["about_defaults"] = resource("about-data.json")
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
    if section == "campaigns" and params.get("id") not in (None, "", NEW_RECORD):
        await load("campaign_donors", "/admin/campaigns/" + quote(params["id"], safe="") + "/donors", {})
    if params.get("id") and section == "events":
        await load("roster", "/admin/events/" + quote(params["id"], safe="") + "/rsvps", [])
    if params.get("id") and section == "members" and full_admin:
        await load("point_history", "/admin/members/" + quote(params["id"], safe="") + "/points", {})


AUDIT_SECTION = "audit"
AUDIT_READS_PARAM = "reads"


def office_rows(data, params, section):
    data["rows"] = section_records(section, page_items(data.get("records")), data["office_names"])
    if section in EDITORS:
        record_editor(data, section, params)
    if section in FIELDS:
        data["office_total"] = len(data["rows"])
        data["rows"] = filter_content(data["rows"], params)
    if section == AUDIT_SECTION:
        data["rows"] = audit_rows(data["rows"], params.get(AUDIT_READS_PARAM) == "1")
    office_pagination(data, params, section)


PAGE_ROUTES = (
    (lambda page: features.closed(page.path), closed_page),
    (lambda page: page.path in LEGAL_FILES, legal_file_page),
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
    (lambda page: page.path == "/about", about_page),
    (lambda page: page.path == "/give", give_page),
    (lambda page: page.template == "campaign.html", campaign_page),
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
        "push_enabled": features.PUSH_ENABLED,
        "dpo_enabled": features.DPO_ENABLED,
        "tabs": mobile_tabs(path),
        "live": live_view(path),
        "office_nav": OFFICE_NAV,
        "office_groups": GROUPS,
        "office_descriptions": DESCRIPTIONS,
        "office_fields": FIELDS,
        "office_choices": CHOICES,
        "office_single": SINGLE_CHOICES,
        "office_people": PEOPLE_CHOICES,
        "office_roles": ROLE_OPTIONS,
        "office_cohorts": {},
        "office_defaults": CREATE_DEFAULTS,
        "office_read_only": READ_ONLY_SECTIONS,
        "office_row_editors": ROW_EDITORS,
        "office_options": {},
        "office_names": {},
        "labels": LABELS,
        "metrics": METRICS,
        "errors": [],
        "load_errors": {},
        "session_unavailable": False,
        "authorized": authorized,
        "status": 404 if template == "not-found.html" else 200,
        "domain": resource("domain-data.json"),
        "legal": resource("legal-data.json"),
        "telegram_channel": TELEGRAM_CHANNEL,
    }
    data["reason_labels"] = {
        **{rule["reason"]: rule["description"] for rule in data["domain"]["point_rules"]},
        "manual": "Начисление клуба",
        "decay": "Списание за неактивность",
        "event_undo": "Отметка участия снята",
    }
    data["point_values"] = {rule["reason"]: rule["points"] for rule in data["domain"]["point_rules"]}

    if not path.startswith("/admin"):
        data["registration_open"] = await registration_open(api)
    page = Page(api, path, params, authorized, template, data)
    handler = next((handler for matches, handler in PAGE_ROUTES if matches(page)), None)
    if handler:
        await handler(page)
    return data
