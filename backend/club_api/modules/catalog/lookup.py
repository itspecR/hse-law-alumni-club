from club_api.modules.catalog.scope import DPO_PROGRAMS


async def lookup_catalog(store, items, connection=None):
    result = {}
    for type, table, extra, scope in (
        ("dpo", "programs", ("enrollment", "source_url"), DPO_PROGRAMS),
        ("merch", "products", ("stock", "variants_json"), {}),
    ):
        slugs = list(dict.fromkeys(item["ref_id"] for item in items if item["type"] == type))
        if not slugs:
            continue
        rows = await store.read(
            table,
            filters={"slug": {"_in": slugs}, "status": {"_eq": "published"}, **scope},
            limit=-1,
            fields=("slug", "title", "price", *extra),
            connection=connection,
        )
        for row in rows:
            result[type + ":" + row["slug"]] = {
                "title": row["title"],
                "price": row["price"] or 0,
                "enrollment": row.get("enrollment"),
                "source_url": row.get("source_url"),
                "stock": row.get("stock"),
                "variants": row.get("variants_json") if isinstance(row.get("variants_json"), list) else [],
            }
    return result
