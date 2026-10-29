from club_web.changes import select_changes
from club_web.formatting import safe_url


def search_sources(portfolio, programs, news, events, podcasts, changes):
    sources = [
        ("portfolio", "Портфель программ", portfolio, ("title", "direction"), "slug"),
        ("dpo", "Программы ДПО", programs, ("title", "direction"), "slug"),
        ("news", "Новости", news, ("title", "excerpt"), "slug"),
        ("events", "События", events, ("title", "description", "location"), "id"),
        ("podcasts", "Подкасты", podcasts, ("title",), "id"),
    ]
    if changes is not None:
        sources.append(
            (
                "changes",
                "Изменения в праве",
                select_changes(changes, {"view": "all"}),
                ("title", "number", "summary"),
                "id",
            )
        )
    return sources


def record_link(prefix, record, identifier):
    if prefix == "dpo":
        return {"path": safe_url(record.get("source_url")) or "/dpo", "external": True}
    return {"path": f"/{prefix}/{record[identifier]}", "external": False}


def search_groups(portfolio, programs, news, events, podcasts, changes, query=None):
    words = (query or "").casefold().replace("ё", "е").split()
    groups = []
    for prefix, label, records, fields, identifier in search_sources(
        portfolio, programs, news, events, podcasts, changes
    ):
        items = []
        for record in records:
            text = " ".join(str(record.get(field) or "") for field in fields)
            if prefix == "changes":
                text += " " + " ".join(
                    segment["text"] for block in record.get("blocks", []) for segment in block["segments"]
                )
            normalized = text.casefold().replace("ё", "е")
            if query is None or len(query.strip()) >= 2 and all(word in normalized for word in words):
                items.append({"title": record["title"], "text": text, **record_link(prefix, record, identifier)})
        groups.append({"label": label, "items": items if query is None else items[:6]})
    return groups
