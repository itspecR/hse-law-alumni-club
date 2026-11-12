from club_api.modules.catalog.scope import PUBLISHED

CAMPAIGN_TABLE = "club_campaigns"
NEWEST_FIRST = ("-created_at", "slug")


async def published(state, fields, filters=None):
    rows = await state.store.read(
        CAMPAIGN_TABLE, filters={**PUBLISHED, **(filters or {})}, fields=fields, sort=NEWEST_FIRST, limit=1
    )
    return rows[0] if rows else None


async def published_by_slug(state, slug, fields):
    return await published(state, fields, {"slug": {"_eq": slug}})


async def payment_purpose(state, campaign_id):
    if not campaign_id:
        return None
    rows = await state.store.read(
        CAMPAIGN_TABLE, filters={"id": {"_eq": campaign_id}}, fields=("payment_purpose",), limit=1
    )
    return (rows[0]["payment_purpose"] or "").strip() or None if rows else None


async def campaign_titles(store):
    rows = await store.read(CAMPAIGN_TABLE, fields=("id", "title"), sort=("title",), limit=-1)
    return {row["id"]: row["title"] for row in rows}
