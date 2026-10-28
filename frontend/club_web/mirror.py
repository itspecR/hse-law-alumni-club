import argparse
import asyncio
import json
import re
from pathlib import Path

import httpx

from club_web.asgi import initialize_django
from club_web.build import build_public
from club_web.calendar import calendar_file
from club_web.client import Api
from club_web.pages import OFFICE_NAV, PUBLIC_PAGES, context, resource
from club_web.rendering import published_version, templates
from club_web.search import search_groups


def fixtures(root):
    data = json.loads((root / "scripts/club_ops/data/demo.json").read_text(encoding="utf-8"))
    data["programs"] = json.loads((root / "data/dpo-catalog.json").read_text(encoding="utf-8"))
    data["podcasts"] = json.loads((root / "frontend/fixtures/podcasts.json").read_text(encoding="utf-8"))
    for name, records in data.items():
        for index, record in enumerate(records):
            record.setdefault("id", f"mirror-{name}-{index + 1}")
            if name in ("programs", "products", "news", "events", "podcasts", "timeline_items"):
                record.setdefault("status", "published")
    return data


def snapshot(data):
    faq = resource("faq-data.json")
    alumni = data["alumni"][0]
    profile = {
        "alumni": {
            **alumni,
            "verification_status": "verified",
            "contacts": alumni.get("contacts_json") or {},
            "interests": alumni.get("interests_json") or [],
            "referral_code": "DEMO",
            "telegram_linked": False,
        },
        "level": {"level_title": "Участник", "points": alumni.get("points_cached", 0), "discount": 5},
        "achievements": [],
        "activity": [],
    }
    result = {
        "/programs": data["programs"],
        "/products": data["products"],
        "/news": data["news"],
        "/events": data["events"],
        "/podcasts": {
            "items": data["podcasts"],
            "subscribed": False,
        },
        "/pages/home": {"blocks": {}},
        "/timeline": data["timeline_items"],
        "/me": profile,
        "/me/orders": [],
        "/me/classmates": [{**row, "friend_status": None} for row in data["alumni"][1:]],
        "/me/events": [],
        "/me/ledger": [],
        "/cart": {"items": [], "subtotal": 0, "count": 0},
        "/auth/admin-session": {"role": "admin"},
        "/support/config": {"enabled": False},
        "/admin/overview": {"alumni_count": len(data["alumni"]), "programs_total": len(data["programs"])},
        "/admin/system-health": {"checks": []},
        "/admin/analytics": {"pulse": {}, "series": {}},
        "/admin/orders": [],
        "/admin/members": data["alumni"],
        "/admin/podcast-subs": {"items": []},
        "/admin/news-sources": {"sources": [], "items": []},
        "/admin/pages/home": {"blocks": {}},
        "/admin/media": [],
        "/admin/audit": [],
        "/admin/bot-status": {
            "siteFaq": {
                "answers": len(faq["answers"]),
                "gaps": len(faq["gaps"]),
                "note": "В демоверсии показан справочник FAQ. Статистика обращений доступна в рабочей панели офиса.",
            },
        },
        "/admin/support": [],
        "/portfolio": [],
        "/admin/portfolio": [],
        "/admin/trainers": [],
        "/admin/promo-codes": [],
    }
    for name in ("programs", "products", "news", "events", "podcasts"):
        result["/admin/" + name] = data[name]
    result["/admin/timeline"] = data["timeline_items"]
    for name in ("programs", "news"):
        for row in data[name]:
            result[f"/{name}/{row['slug']}"] = row
    return result


def relocate(html, base):
    def replace(match):
        url = match[2]
        return match[1] + (url if url.startswith(base) else base + url.lstrip("/"))

    return re.sub(r'(?<![\w-])((?:href|src|action)=["\'])(/(?!/)[^"\']*)', replace, html)


async def export(destination, base, data):
    destination = Path(destination)
    base = "/" + base.strip("/") + "/" if base.strip("/") else "/"
    build_public(destination, mirror=True)
    endpoints = snapshot(data)
    calendar_directory = destination / "calendar"
    calendar_directory.mkdir(exist_ok=True)
    for event in data["events"]:
        (calendar_directory / (event["id"] + ".ics")).write_bytes(
            calendar_file(event, "https://bogolubov-creator.github.io" + base.rstrip("/")).encode()
        )
    (destination / "data").mkdir(exist_ok=True)
    (destination / "data/mirror-api.json").write_text(json.dumps(endpoints, ensure_ascii=False), encoding="utf-8")
    changes = resource("changes.json")
    (destination / "data/changes.json").write_text(
        json.dumps(changes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_path = destination / "manifest.webmanifest"
    manifest = json.loads(manifest_path.read_text())
    manifest.update(start_url=base + "tg?pwa=1", scope=base, id=base)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    def upstream(request):
        path = request.url.path
        return (
            httpx.Response(200, json=endpoints[path])
            if path in endpoints
            else httpx.Response(404, json={"error": "Не найдено"})
        )

    paths = list(PUBLIC_PAGES) + ["/admin" + ("/" + key if key != "overview" else "") for key, _ in OFFICE_NAV]
    for prefix, key, identifier in (
        ("dpo", "programs", "slug"),
        ("merch", "products", "slug"),
        ("news", "news", "slug"),
        ("events", "events", "id"),
        ("podcasts", "podcasts", "id"),
    ):
        paths.extend(f"/{prefix}/{row[identifier]}" for row in data[key])
    paths.extend("/changes/" + row["id"] for row in changes["items"])
    paths.append("/404")
    initialize_django()
    renderer = templates()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="http://mirror.test") as client:
        for path in paths:
            page = await context(
                Api(client, {"authorization": "Bearer demo", "x-cart-session": "demo"}), path, {}, True
            )
            if page["errors"]:
                raise ValueError(f"Не удалось собрать страницу {path}: {'; '.join(page['errors'])}")
            if path == "/changes" or path.startswith("/changes/"):
                page["items"] = changes["items"]
                page["next_page"] = ""
            if path == "/search":
                page["groups"] = search_groups(
                    [], data["programs"], data["news"], data["events"], data["podcasts"], changes["items"]
                )
            page.update(base=base, mirror=True, request=None, asset_version=published_version(destination))
            target = destination / path.lstrip("/") / "index.html" if path != "/404" else destination / "404.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                relocate(renderer.env.get_template(page["template"]).render(**page, fragment=False), base),
                encoding="utf-8",
            )
            fragment = destination / "views" / ((path.strip("/") or "home") + ".html")
            fragment.parent.mkdir(parents=True, exist_ok=True)
            fragment.write_text(
                relocate(renderer.env.get_template(page["template"]).render(**page, fragment=True), base),
                encoding="utf-8",
            )
    (destination / ".nojekyll").touch()
    print(f"Зеркало: {len(paths)} страниц, отправка данных отключена")


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base", default="/club-pravo-hse-mirror/")
    parser.add_argument("--fixtures", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    data = json.loads(args.fixtures.read_text()) if args.fixtures else fixtures(root)
    asyncio.run(export(args.output, args.base, data))


if __name__ == "__main__":
    run()
