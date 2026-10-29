import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from club_web import features
from club_web.calendar import google_calendar, instant, month_view
from club_web.catalog import UPCOMING_LIMIT, prepare_catalog
from club_web.changes import parse_changes, select_changes
from club_web.client import ApiFailure
from club_web.formatting import page_items, rub
from club_web.office import (
    CHOICES,
    CREATE_DEFAULTS,
    DESCRIPTIONS,
    FIELDS,
    GROUPS,
    ICONS,
    LABELS,
    METRICS,
    SECTION_FILTERS,
    SECTION_LABELS,
    SINGLE_CHOICES,
    choice_options,
    choice_sources,
    cohort_options,
    filter_content,
    section_records,
    status_values,
)
from club_web.pricing import member_prices
from club_web.search import search_groups

HOME_EPISODES = 4
HOME_CHANGES = 3
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
    "/confidential": ("legal/confidential.html", "Политика конфиденциальности"),
    "/requisites": ("legal/requisites.html", "Реквизиты"),
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
    ("timeline", "История"),
    ("pages", "Страницы"),
    ("media", "Медиа"),
    ("audit", "Журнал"),
    ("support", "Поддержка"),
]
OFFICE_ENDPOINTS = {
    "overview": "/admin/overview",
    "analytics": "/admin/analytics",
    "orders": "/admin/orders",
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
    "timeline": "/admin/timeline",
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
        ("/dpo/", "program.html"),
        ("/portfolio/", "portfolio-program.html"),
        ("/merch/", "product.html"),
        ("/news/", "article.html"),
        ("/events/", "events.html"),
        ("/podcasts/", "episode.html"),
        ("/changes/", "changes.html"),
    ):
        if path.startswith(prefix) and "/" not in path[len(prefix) :] and path[len(prefix) :]:
            return template, "Клуб выпускников"
    return "not-found.html", "Страница не найдена"


def with_member_prices(program, levels):
    rows = member_prices(program["price"], levels)
    return {**program, "member_prices": rows, "member_price_map": {row["level"]: rub(row["price"]) for row in rows}}


def home_programs(programs, levels, today=None):
    catalog = prepare_catalog([item for item in programs if item.get("price") is not None], {}, today)
    starts = catalog["upcoming"]
    shown = starts or [item for item in catalog["items"] if item.get("enrollment") == "actual"][:UPCOMING_LIMIT]
    return {
        "levels": sorted(levels, key=lambda level: level["sort"]),
        "home_starts": bool(starts),
        "home_programs": [with_member_prices(item, levels) for item in shown],
        "programs_total": len(programs),
        "programs_open": sum(item.get("enrollment") == "actual" for item in programs),
    }


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


def navigation():
    return [(href, label) for href, label in NAV if features.section_enabled(href)]


def changes_items():
    return parse_changes(resource("changes.json"))["items"] if features.CHANGES_ENABLED else None


async def context(api, path, params, authorized=False):
    template, title = select_page(path)
    data = {
        "template": template,
        "title": title,
        "path": path,
        "query": params,
        "nav": navigation(),
        "changes_enabled": features.CHANGES_ENABLED,
        "live": live_view(path),
        "office_nav": OFFICE_NAV,
        "office_groups": GROUPS,
        "office_descriptions": DESCRIPTIONS,
        "office_icons": ICONS,
        "office_fields": FIELDS,
        "office_choices": CHOICES,
        "office_single": SINGLE_CHOICES,
        "office_cohorts": {},
        "office_defaults": CREATE_DEFAULTS,
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
    }

    async def load(key, endpoint, default=None):
        try:
            data[key] = await api.get(endpoint)
        except ApiFailure as error:
            data[key] = default
            data["load_errors"][key] = error.status
            data["errors"].append(error.message)
            if error.status in (401, 403):
                data["access_error"] = error.status
            if key == "item" and error.status >= 500:
                data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
            if key == "item" and error.status == 404:
                data.update(template="not-found.html", title="Страница не найдена", status=404)
        return data[key]

    if path == "/":
        await asyncio.gather(
            load("page", "/pages/home", {}),
            load("news", "/news?limit=3", []),
            load("programs", "/programs", []),
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
        data["episodes"] = page_items(data["podcasts"])[:HOME_EPISODES]
        data["recent_changes"] = recent_changes() if features.CHANGES_ENABLED else []
    elif path in ("/dpo", "/merch"):
        kind = "dpo" if path == "/dpo" else "merch"
        await load("items", "/programs" if kind == "dpo" else "/products", [])
        data["kind"] = kind
        if kind == "dpo":
            data.update(prepare_catalog(data["items"], params))
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
    elif path == "/portfolio":
        await load("items", "/portfolio", [])
    elif path.startswith("/portfolio/"):
        await load("item", "/portfolio/" + quote(path.rsplit("/", 1)[1], safe=""), {})
        if data["item"]:
            data["title"] = data["item"]["title"]
    elif path.startswith(("/dpo/", "/news/")):
        endpoint = "/programs/" if path.startswith("/dpo/") else "/news/"
        await load("item", endpoint + quote(path.rsplit("/", 1)[1], safe=""), {})
        if data["item"]:
            data["title"] = data["item"]["title"]
    elif path.startswith("/merch/"):
        await load("products", "/products", [])
        data["item"] = next((item for item in data["products"] if item["slug"] == path.rsplit("/", 1)[1]), None)
        if data["item"]:
            data["title"] = data["item"]["title"]
        elif "products" in data["load_errors"]:
            data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
        else:
            data.update(template="not-found.html", status=404)
    elif path == "/search":
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
    elif path == "/news":
        await load("items", "/news", [])
    elif path == "/events" or path.startswith("/events/"):
        event_id = path.removeprefix("/events/") if path != "/events" else params.get("event")
        endpoint = "/events"
        if event_id:
            endpoint += "?id=" + quote(event_id, safe="")
        elif params.get("month"):
            endpoint += "?month=" + month_view([], params["month"])["selected"]
        await load("items", endpoint, [])
        for event in data["items"]:
            event["calendar_url"] = google_calendar(event)
            event["past"] = event.get("status") in ("done", "canceled") or instant(
                event.get("starts_at")
            ) < datetime.now(UTC)
        data["calendar"] = month_view(data["items"], params.get("month"))
        data["item"] = next((item for item in data["items"] if item["id"] == event_id), None)
        if event_id and "items" in data["load_errors"]:
            data.update(template="unavailable.html", title="Не удалось загрузить страницу", status=503)
        elif event_id and not data["item"]:
            data.update(template="not-found.html", status=404)
    elif path == "/podcasts" or path.startswith("/podcasts/"):
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
    elif not features.section_enabled(path):
        data["template"] = "changes-soon.html"
    elif path == "/changes" or path.startswith("/changes/"):
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
    elif path in ("/lk", "/lk/profile") and authorized:
        await load("me", "/me", {})
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
    elif path == "/cart" and "x-cart-session" in api.headers:
        await load("cart", "/cart", {"items": [], "subtotal": 0})
    elif path == "/support":
        await load("support", "/support/config", {"enabled": False})
        data["faq"] = resource("faq-data.json")
    elif path.startswith("/admin"):
        section = path.removeprefix("/admin/") if path != "/admin" else "overview"
        data["section"] = section
        data["labels"] = {**LABELS, **SECTION_LABELS.get(section, {})}
        data["office_template"] = "office/" + ("content" if section in FIELDS else section) + ".html"
        data["office_statuses"] = status_values(section)
        data["office_filters"] = SECTION_FILTERS.get(section, ())
        if authorized and template != "not-found.html":
            await load("session", "/auth/admin-session", {})
            if data["session"]:
                data["role"] = data["session"]["role"]
                if section == "support":
                    await load("bot", "/admin/bot-status", {})
                if section == "support" and data["role"] not in ("admin", "Administrator"):
                    data["records"] = []
                elif section == "pages":
                    await load("record", "/admin/pages/home", {})
                else:
                    query = urlencode(
                        {key: params[key] for key in ("q", "status", "payment", "page", "range") if key in params}
                    )
                    await load("records", OFFICE_ENDPOINTS[section] + ("?" + query if query else ""), [])
                if section == "overview":
                    await load("health", "/admin/system-health", {})
                for source in choice_sources(section):
                    rows = page_items(await load("choices:" + source, OFFICE_ENDPOINTS[source], []))
                    data["office_options"][source] = choice_options(source, rows)
                    data["office_names"][source] = {row["id"]: row["label"] for row in data["office_options"][source]}
                if section == "orders":
                    data["office_cohorts"] = cohort_options(page_items(await load("cohorts", "/admin/cohorts", [])))
                if params.get("id") and section == "events":
                    await load("roster", "/admin/events/" + quote(params["id"], safe="") + "/rsvps", [])
                data["rows"] = section_records(section, page_items(data.get("records")), data["office_names"])
                if section in FIELDS:
                    data["office_total"] = len(data["rows"])
                    data["rows"] = filter_content(data["rows"], params)
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
    return data
