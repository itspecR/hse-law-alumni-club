import asyncio
import time
from urllib.parse import quote
from xml.sax.saxutils import escape

from django.http import HttpRequest, HttpResponse

from club_api.core.errors import ApiError
from club_api.core.views import api_view, query_integer
from club_api.modules.catalog.scope import PUBLISHED_DPO, PUBLISHED_PORTFOLIO

NEWS_FIELDS = ("id", "slug", "title", "excerpt", "body", "published_at", "source_url", "cover", "origin")
PROGRAM_FIELDS = (
    "id",
    "slug",
    "title",
    "direction",
    "format",
    "duration",
    "price",
    "enrollment",
    "source_url",
    "dates",
    "document",
    "description",
    "cover",
    "tagline",
    "hse_id",
)
PRODUCT_FIELDS = ("id", "slug", "title", "category", "price", "images", "variants_json", "stock", "description")
PUBLISHED = {"status": {"_eq": "published"}}


@api_view
async def robots(request: HttpRequest):
    text = "User-agent: *\nAllow: /\nDisallow: /lk\nDisallow: /admin\nDisallow: /cart\nDisallow: /api/\n"
    text += f"Sitemap: {request.services.settings.PUBLIC_URL.rstrip('/')}/sitemap.xml\n"
    return HttpResponse(text, content_type="text/plain")


@api_view
async def sitemap(request: HttpRequest):
    state = request.services
    cached = getattr(state, "sitemap_cache", None)
    now = time.monotonic()
    if cached and now - cached[0] < 3600:
        return HttpResponse(cached[1], content_type="application/xml")
    news, portfolio = await asyncio.gather(
        state.store.read("news", filters=PUBLISHED, fields=("slug", "published_at"), limit=-1),
        state.store.read("programs", filters=PUBLISHED_PORTFOLIO, fields=("slug",), limit=-1),
    )
    urls = [
        ("/", "1.0", "weekly", None),
        ("/portfolio", "0.9", "weekly", None),
        ("/events", "0.9", "weekly", None),
        ("/dpo", "0.9", "weekly", None),
        ("/news", "0.8", "weekly", None),
        ("/join", "0.8", "monthly", None),
        ("/podcasts", "0.7", "weekly", None),
        ("/merch", "0.7", "monthly", None),
        *[
            (
                "/news/" + quote(item["slug"], safe=""),
                "0.6",
                "monthly",
                item["published_at"][:10] if item["published_at"] else None,
            )
            for item in news
        ],
        *[("/portfolio/" + quote(item["slug"], safe=""), "0.6", "monthly", None) for item in portfolio],
        *[(path, "0.3", "yearly", None) for path in ("/privacy", "/confidential", "/requisites")],
    ]
    base = state.settings.PUBLIC_URL.rstrip("/")
    entries = []
    for path, priority, frequency, modified in urls:
        date = f"<lastmod>{escape(modified)}</lastmod>" if modified else ""
        entries.append(
            f"<url><loc>{escape(base + path)}</loc>{date}<changefreq>{frequency}</changefreq><priority>{priority}</priority></url>"
        )
    xml = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            *entries,
            "</urlset>",
        ]
    )
    state.sitemap_cache = (now, xml)
    return HttpResponse(xml, content_type="application/xml")


@api_view
async def news(request: HttpRequest):
    limit = query_integer(request, "limit", minimum=1, maximum=100)
    return await request.services.store.read(
        "news", filters=PUBLISHED, fields=NEWS_FIELDS, sort=("-published_at",), limit=limit or -1
    )


@api_view
async def news_detail(request: HttpRequest, slug: str):
    rows = await request.services.store.read(
        "news", filters={**PUBLISHED, "slug": {"_eq": slug}}, fields=NEWS_FIELDS, limit=1
    )
    if not rows:
        raise ApiError(404, "Новость не найдена.")
    return rows[0]


@api_view
async def programs(request: HttpRequest):
    return await request.services.store.read(
        "programs", filters=PUBLISHED_DPO, fields=PROGRAM_FIELDS, sort=("title",), limit=-1
    )


@api_view
async def program_detail(request: HttpRequest, slug: str):
    rows = await request.services.store.read(
        "programs",
        filters={**PUBLISHED_DPO, "slug": {"_eq": slug}},
        fields=(*PROGRAM_FIELDS, "modules", "teachers", "audience", "results", "advantages"),
        limit=1,
    )
    if not rows:
        raise ApiError(404, "Программа не найдена.")
    return rows[0]


@api_view
async def products(request: HttpRequest):
    return await request.services.store.read(
        "products", filters=PUBLISHED, fields=PRODUCT_FIELDS, sort=("title",), limit=-1
    )


@api_view
async def timeline(request: HttpRequest):
    return await request.services.store.read(
        "timeline_items",
        filters=PUBLISHED,
        fields=("id", "year", "title", "text", "metric", "sort"),
        sort=("sort",),
        limit=-1,
    )


@api_view
async def page(request: HttpRequest, slug: str):
    rows = await request.services.store.read(
        "pages",
        filters={**PUBLISHED, "slug": {"_eq": slug}},
        limit=1,
        fields=(
            "id",
            "slug",
            "title",
            "blocks.collection",
            "blocks.sort",
            "blocks.item:block_hero.*",
            "blocks.item:block_cta.*",
        ),
    )
    if not rows:
        raise ApiError(404, "Страница не найдена.")
    page = rows[0]
    blocks = {
        block["collection"].removeprefix("block_"): block["item"]
        for block in page["blocks"]
        if block["collection"] and block["item"]
    }
    return {"slug": page["slug"], "title": page["title"], "blocks": blocks}
