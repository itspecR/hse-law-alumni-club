import asyncio
import time
from urllib.parse import quote
from xml.sax.saxutils import escape

from django.http import HttpRequest, HttpResponse

from club_api.core.views import api_view
from club_api.modules.campaigns.store import CAMPAIGN_TABLE
from club_api.modules.catalog.scope import PUBLISHED, PUBLISHED_PORTFOLIO

CACHE_SECONDS = 3600
FACULTY_ORIGIN = "faculty"
SHOWN_EVENTS = {"status": {"_in": ["published", "done"]}}
HIDDEN_PATHS = (
    "/lk",
    "/admin",
    "/cart",
    "/saved",
    "/search",
    "/pay/",
    "/reset",
    "/confirm",
    "/forgot",
    "/views/",
    "/support/consent",
    "/give/thanks",
    "/api/",
)
OPEN_PATHS = ("/api/media/",)
CLEAN_PARAMS = "utm_source&utm_medium&utm_campaign&utm_content&utm_term&source"
ALWAYS = (
    ("/", "1.0", "weekly"),
    ("/join", "0.8", "monthly"),
    ("/give", "0.8", "monthly"),
    ("/support", "0.5", "monthly"),
    ("/documents", "0.3", "yearly"),
    ("/requisites", "0.3", "yearly"),
)
SECTIONS = (
    ("/portfolio", "portfolio", "0.9", "weekly"),
    ("/events", "events", "0.9", "weekly"),
    ("/news", "news", "0.8", "weekly"),
    ("/podcasts", "podcasts", "0.7", "weekly"),
    ("/merch", "products", "0.7", "monthly"),
)
RECORD_PRIORITY = "0.6"
RECORD_FREQUENCY = "monthly"


def robots_text(public_url):
    lines = ["User-agent: *", "Allow: /"]
    lines += ["Allow: " + path for path in OPEN_PATHS]
    lines += ["Disallow: " + path for path in HIDDEN_PATHS]
    lines += ["Clean-param: " + CLEAN_PARAMS, "Sitemap: " + public_url + "/sitemap.xml"]
    return "\n".join(lines) + "\n"


@api_view
async def robots(request: HttpRequest):
    return HttpResponse(robots_text(request.services.settings.PUBLIC_URL.rstrip("/")), content_type="text/plain")


async def records(store):
    news, portfolio, events, podcasts, products, campaigns = await asyncio.gather(
        store.read("news", filters=PUBLISHED, fields=("slug", "published_at", "origin"), limit=-1),
        store.read("programs", filters=PUBLISHED_PORTFOLIO, fields=("slug",), limit=-1),
        store.read("events", filters=SHOWN_EVENTS, fields=("id", "slug"), limit=-1),
        store.read("podcasts", filters=PUBLISHED, fields=("id", "slug"), limit=-1),
        store.read("products", filters=PUBLISHED, fields=("slug",), limit=-1),
        store.read(CAMPAIGN_TABLE, filters=PUBLISHED, fields=("slug",), limit=-1),
    )
    return {
        "news": news,
        "portfolio": portfolio,
        "events": events,
        "podcasts": podcasts,
        "products": products,
        "campaigns": campaigns,
    }


def address(item):
    return str(item.get("slug") or item["id"])


def record_urls(found):
    own_news = [item for item in found["news"] if item.get("origin") != FACULTY_ORIGIN]
    dated = [("/news/" + quote(item["slug"], safe=""), (item["published_at"] or "")[:10]) for item in own_news]
    plain = [
        *("/portfolio/" + quote(item["slug"], safe="") for item in found["portfolio"]),
        *("/events/" + quote(address(item), safe="") for item in found["events"]),
        *("/podcasts/" + quote(address(item), safe="") for item in found["podcasts"]),
        *("/merch/" + quote(item["slug"], safe="") for item in found["products"]),
        *("/give/" + quote(item["slug"], safe="") for item in found["campaigns"]),
    ]
    return [(path, RECORD_PRIORITY, RECORD_FREQUENCY, modified) for path, modified in dated] + [
        (path, RECORD_PRIORITY, RECORD_FREQUENCY, "") for path in plain
    ]


def site_urls(found):
    sections = [(path, priority, frequency, "") for path, kind, priority, frequency in SECTIONS if found[kind]]
    return [*((path, priority, frequency, "") for path, priority, frequency in ALWAYS), *sections, *record_urls(found)]


def entry(base, path, priority, frequency, modified):
    date = f"<lastmod>{escape(modified)}</lastmod>" if modified else ""
    return (
        f"<url><loc>{escape(base + path)}</loc>{date}"
        f"<changefreq>{frequency}</changefreq><priority>{priority}</priority></url>"
    )


def sitemap_xml(base, urls):
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            *(entry(base, *url) for url in sorted(urls, key=lambda url: (-float(url[1]), url[0]))),
            "</urlset>",
        ]
    )


@api_view
async def sitemap(request: HttpRequest):
    state = request.services
    cached = getattr(state, "sitemap_cache", None)
    now = time.monotonic()
    if cached and now - cached[0] < CACHE_SECONDS:
        return HttpResponse(cached[1], content_type="application/xml")
    xml = sitemap_xml(state.settings.PUBLIC_URL.rstrip("/"), site_urls(await records(state.store)))
    state.sitemap_cache = (now, xml)
    return HttpResponse(xml, content_type="application/xml")
