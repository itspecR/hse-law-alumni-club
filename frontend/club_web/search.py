from club_web.changes import select_changes


def search_groups(portfolio, programs, news, events, podcasts, changes, query=None):
    words = (query or "").casefold().replace("ё", "е").split()
    groups = []
    changes = select_changes(changes, {"view": "all"})
    for prefix, label, records, fields, identifier in (
        ("portfolio", "Портфель программ", portfolio, ("title", "direction"), "slug"),
        ("dpo", "Программы ДПО", programs, ("title", "direction"), "slug"),
        ("news", "Новости", news, ("title", "excerpt"), "slug"),
        ("events", "События", events, ("title", "description", "location"), "id"),
        ("podcasts", "Подкасты", podcasts, ("title",), "id"),
        ("changes", "Изменения в праве", changes, ("title", "number", "summary"), "id"),
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
                items.append({"title": record["title"], "path": f"/{prefix}/{record[identifier]}", "text": text})
        groups.append({"label": label, "items": items if query is None else items[:6]})
    return groups
