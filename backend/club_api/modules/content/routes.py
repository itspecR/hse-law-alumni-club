from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view, query_integer
from club_api.modules.catalog.scope import PUBLISHED_DPO

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

PAGE_TEXTS = ("body", "meta_title", "meta_description")


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
async def page(request: HttpRequest, slug: str):
    rows = await request.services.store.read(
        "pages",
        filters={**PUBLISHED, "slug": {"_eq": slug}},
        limit=1,
        fields=(
            "id",
            "slug",
            "title",
            *PAGE_TEXTS,
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
    return {"slug": page["slug"], "title": page["title"], **{name: page[name] for name in PAGE_TEXTS}, "blocks": blocks}
