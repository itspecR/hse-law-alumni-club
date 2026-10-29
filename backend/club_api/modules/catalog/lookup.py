async def lookup_catalog(store, items, connection=None):
    slugs = list(dict.fromkeys(item["ref_id"] for item in items if item["type"] == "merch"))
    if not slugs:
        return {}
    rows = await store.read(
        "products",
        filters={"slug": {"_in": slugs}, "status": {"_eq": "published"}},
        limit=-1,
        fields=("slug", "title", "price", "stock", "variants_json"),
        connection=connection,
    )
    return {
        "merch:" + row["slug"]: {
            "title": row["title"],
            "price": row["price"] or 0,
            "stock": row.get("stock"),
            "variants": row.get("variants_json") if isinstance(row.get("variants_json"), list) else [],
        }
        for row in rows
    }
