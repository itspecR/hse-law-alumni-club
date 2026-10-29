import html
import math
import re

from club_api.modules.catalog.scope import PUBLISHED_DPO
from club_api.modules.support.service import FAQ, log_faq

ENDINGS = (
    "ниями",
    "ениям",
    "ования",
    "ование",
    "ением",
    "ения",
    "ение",
    "ями",
    "ами",
    "ого",
    "ому",
    "ыми",
    "ими",
    "ей",
    "ов",
    "ев",
    "ый",
    "ий",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ых",
    "их",
    "ой",
    "ом",
    "ам",
    "ах",
    "ям",
    "ях",
    "ы",
    "и",
    "а",
    "я",
    "о",
    "е",
    "у",
    "ю",
    "ь",
)
BOT_FIELDS = (
    "id",
    "slug",
    "title",
    "direction",
    "format",
    "duration",
    "price",
    "document",
    "dates",
    "description",
    "modules",
    "source_url",
)


def stem(value):
    value = value.lower().replace("ё", "е")
    return next((value[: -len(end)] for end in ENDINGS if len(value) - len(end) >= 4 and value.endswith(end)), value)


def same_stem(left, right):
    return left == right or (
        min(len(left), len(right)) >= 4
        and abs(len(left) - len(right)) >= 2
        and (left.startswith(right) or right.startswith(left))
    )


def tokenize(value, minimum=2):
    return [
        stem(word)
        for word in re.sub(r"[^а-яa-z0-9\s]", " ", value.lower().replace("ё", "е")).split()
        if len(word) >= minimum and not word.isdigit()
    ]


def trigger_matches(trigger, tokens):
    words = tokenize(trigger)
    return bool(words) and all(any(same_stem(word, token) for token in tokens) for word in words)


def find_trigger(tokens, items):
    return next((item for item in items if any(trigger_matches(trigger, tokens) for trigger in item["triggers"])), None)


def consume_word(value, start, end):
    while start > 0 and re.match(r"[а-яa-z0-9]", value[start - 1]):
        start -= 1
    while end < len(value) and re.match(r"[а-яa-z0-9]", value[end]):
        end += 1
    return value[:start] + " " + value[end:]


def parse_query(query):
    remaining = query.lower().replace("ё", "е")
    result = {"stems": [], "priceMax": None, "priceMin": None, "format": None, "type": None}
    match = re.search(
        r"(?<![а-яa-z])(до|дешевле|не дороже|не больше|за|от|дороже)\s+([0-9][0-9\s]*)\s*(тыс\w*|руб\w*|₽)?",
        remaining,
        re.ASCII,
    )
    if match:
        value = int(re.sub(r"\s", "", match[2]))
        tail = remaining[match.end() : match.end() + 12]
        duration = re.match(r"\s*(месяц|недел|год|лет(?=$|[^а-яё])|час|дн)", tail)
        if not duration and (match[3] or value >= 1000):
            if match[3] and "тыс" in match[3]:
                value *= 1000
            result["priceMin" if not match[1].startswith("не ") and match[1] in ("от", "дороже") else "priceMax"] = (
                value
            )
            remaining = consume_word(remaining, match.start(), match.start() + len(match[0].rstrip()))
    for pattern, value in (
        (r"онлайн|дистанц|удал", "online"),
        (r"очн|офлайн|аудитор", "offline"),
        (r"смешан", "mixed"),
        (r"гибрид", "mixed"),
    ):
        match = re.search(pattern, remaining)
        if match:
            result["format"] = value
            remaining = consume_word(remaining, match.start(), match.end())
            break
    match = re.search(r"переподготовк[а-яa-z]*|новая профессия", remaining)
    if match:
        result["type"] = "ПП"
    else:
        match = re.search(r"повышение квалификац[а-яa-z]*", remaining)
        if match:
            result["type"] = "ПК"
        else:
            match = re.search(r"(^|[^а-яa-z0-9])(пп|пк)(?=$|[^а-яa-z0-9])", remaining)
            if match:
                result["type"] = match[2].upper()
    if match:
        remaining = consume_word(remaining, match.start(), match.end())
    stops = [
        stem(word)
        for word in (
            "программа",
            "курс",
            "обучение",
            "формат",
            "подобрать",
            "какой",
            "нужен",
            "документ",
            "старт",
            "стоит",
            "цена",
        )
    ]
    result["stems"] = [word for word in tokenize(remaining, 3) if not any(same_stem(word, stop) for stop in stops)]
    return result


def thousands(value):
    return f"{value:,}".replace(",", " ")


def program_to_bot(program):
    raw_format = (program.get("format") or "").lower()
    format = (
        "online"
        if re.search(r"онлайн|online|дистанц", raw_format)
        else "mixed"
        if re.search(r"смешан|mixed|blended|гибрид", raw_format)
        else "offline"
        if re.search(r"очн|offline|офлайн", raw_format)
        else raw_format or "offline"
    )
    document = ((program.get("document") or "") + " " + program["title"]).lower()
    type = (
        "ПП" if "переподготов" in document else "ПК" if re.search(r"повышен|удостоверен|квалификац", document) else ""
    )
    keywords = [program["description"]] if program.get("description") else []
    keywords.extend(
        item if isinstance(item, str) else item["title"]
        for item in program.get("modules") or []
        if isinstance(item, str) or isinstance(item, dict) and item.get("title")
    )
    start = (program.get("dates") or {}).get("start")
    return {
        "id": program["id"],
        "title": program["title"],
        "url": program.get("source_url") or "/dpo",
        "sphere": program.get("direction") or "Программы ДПО",
        "type": type,
        "format": format,
        "formatLabel": program.get("format"),
        "price": math.floor(program["price"] / 100 + 0.5) if program.get("price") is not None else None,
        "duration": program.get("duration"),
        "start": "Старт: " + start if start else None,
        "startIso": None,
        "keywords": keywords,
    }


def search(query, programs):
    parsed = parse_query(query)
    restrictions = any(parsed[field] is not None for field in ("priceMax", "priceMin", "format", "type"))
    if not parsed["stems"] and not restrictions:
        return {"reason": "empty", "programs": []}
    filtered = [
        program
        for program in programs
        if (not parsed["format"] or program["format"] == parsed["format"])
        and (not parsed["type"] or program["type"] == parsed["type"])
        and (parsed["priceMax"] is None or program["price"] is not None and program["price"] <= parsed["priceMax"])
        and (parsed["priceMin"] is None or program["price"] is not None and program["price"] >= parsed["priceMin"])
    ]

    def hits(text):
        words = tokenize(text, 1)
        return sum(any(same_stem(stem, word) for word in words) for stem in parsed["stems"])

    scores = [
        (program, hits(program["title"]), hits(" ".join(program["keywords"])), hits(program["sphere"]))
        for program in filtered
    ]
    ranked = sorted(
        [row for row in scores if row[1] * 3 + row[2] * 2 + row[3] > 0],
        key=lambda row: (-int(row[1] > 0), -(row[1] * 3 + row[2] * 2 + row[3])),
    )
    if ranked:
        return {"reason": "title" if ranked[0][1] else "keywords", "programs": [row[0] for row in ranked]}
    if restrictions and filtered:
        return {"reason": "filter", "programs": filtered}
    return {"reason": "none", "programs": sorted(programs, key=lambda row: not bool(row["start"]))[:3]}


def duration_text(programs):
    lines = []
    for type, title in (("ПК", "Повышение квалификации"), ("ПП", "Профессиональная переподготовка")):
        items = []
        for program in programs:
            raw = program.get("duration") or ""
            match = re.match(r"^([0-9]+(?:,[0-9]+)?)\s+(\S+)", raw.strip())
            if program["type"] != type or not match:
                continue
            word = match[2]
            root = (
                "недел"
                if word.startswith("недел")
                else "месяц"
                if word.startswith("месяц")
                else "год"
                if word.startswith(("год", "лет"))
                else word
            )
            items.append(
                (
                    float(match[1].replace(",", ".")) * {"недел": 7, "месяц": 30, "год": 365}.get(root, 1),
                    raw.strip(),
                    match[1],
                    root,
                )
            )
        if items:
            minimum, maximum = min(items, key=lambda row: row[0]), max(items, key=lambda row: row[0])
            text = (
                minimum[1]
                if minimum[1] == maximum[1]
                else f"{minimum[2]}-{maximum[1]}"
                if minimum[3] == maximum[3]
                else f"{minimum[1]} - {maximum[1]}"
            )
            lines.append(f"{title}: длительность обычно {text}.")
    return "\n".join(lines)


async def site_reply(state, query):
    tokens = tokenize(query)
    duration = any(trigger_matches(trigger, tokens) for trigger in FAQ.get("duration", {}).get("triggers", []))
    answer = find_trigger(tokens, FAQ["answers"]) if not duration else None
    gap = find_trigger(tokens, FAQ["gaps"]) if not duration and not answer else None
    if answer:
        return {"text": answer["text"], "anchor": answer["anchor"], "programs": []}
    if gap:
        await log_faq(state, kind="gap", gap_id=gap["id"], channel="site")
        return {
            "text": "В материалах сайта нет ответа на этот вопрос. Обратитесь в поддержку.",
            "anchor": "/support",
            "programs": [],
        }
    programs = [
        program_to_bot(row)
        for row in await state.store.read(
            "programs",
            filters=PUBLISHED_DPO,
            fields=BOT_FIELDS,
            limit=-1,
        )
    ]
    if duration and (text := duration_text(programs)):
        return {"text": text, "anchor": "/dpo", "programs": []}
    if re.search(r"подобрать программ|выбрать программ", query, re.I):
        return {
            "text": "Выберите сферу или тип программы:",
            "hints": sorted({program["sphere"] for program in programs}) + ["Повышение квалификации", "Переподготовка"],
            "programs": [],
        }
    if re.search(r"сколько стоят программ|цены программ|диапазон цен", query, re.I):
        prices = [program["price"] for program in programs if program["price"] is not None]
        text = (
            f"Программы стоят от {thousands(min(prices))} до {thousands(max(prices))} ₽. Чтобы отобрать по цене, напишите, например, «до 30000» или «от 50000»."
            if prices
            else "Цен сейчас нет в каталоге. Посмотрите раздел «Программы ДПО»."
        )
        return {"text": text, "anchor": "/dpo", "programs": []}
    if re.search(r"ближайш.*старт|начал.*обучени", query, re.I):
        selected = sorted([program for program in programs if program["start"]], key=lambda item: item["start"])[:5]
        return {
            "text": "Ближайшие старты:" if selected else "Дат старта в каталоге сейчас нет.",
            "anchor": "/dpo",
            "programs": selected,
        }
    found = search(query, programs)
    if found["reason"] not in ("none", "empty"):
        return {
            "text": "Отобраны по вашим условиям:" if found["reason"] == "filter" else "Подходящие программы:",
            "anchor": "/dpo",
            "programs": found["programs"][:5],
        }
    await log_faq(state, kind="none", channel="site")
    return {
        "text": "Ответ не найден. Уточните вопрос или обратитесь в поддержку.",
        "anchor": "/support",
        "programs": found["programs"][:3],
    }


async def answer_faq(state, query):
    query = query.strip()
    if len(query) < 2:
        return None
    tokens = tokenize(query)
    duration = any(trigger_matches(trigger, tokens) for trigger in FAQ.get("duration", {}).get("triggers", []))
    answer = find_trigger(tokens, FAQ["answers"]) if not duration else None
    gap = find_trigger(tokens, FAQ["gaps"]) if not duration and not answer else None
    base = state.settings.PUBLIC_URL.rstrip("/")

    def link(path, label):
        return f'<a href="{html.escape(path if path.startswith("https://") else base + path)}">{html.escape(label)}</a>'

    if answer:
        return html.escape(answer["text"]) + "\n\n🌐 " + link(answer["anchor"], "Подробнее")
    if gap:
        await log_faq(state, kind="gap", gap_id=gap["id"], channel="telegram")
        return (
            "В материалах сайта нет ответа на этот вопрос.\nОбратитесь в поддержку: "
            + link("/support", "открыть обращение")
            + "."
        )
    programs = [
        program_to_bot(row)
        for row in await state.store.read(
            "programs",
            filters=PUBLISHED_DPO,
            fields=BOT_FIELDS,
            limit=-1,
        )
    ]
    if duration and (text := duration_text(programs)):
        return html.escape(text) + "\n\n🌐 " + link(FAQ["duration"]["anchor"], "Программы ДПО")
    if duration:
        answer, gap = find_trigger(tokens, FAQ["answers"]), find_trigger(tokens, FAQ["gaps"])
        if answer:
            return html.escape(answer["text"]) + "\n\n🌐 " + link(answer["anchor"], "Подробнее")
        if gap:
            await log_faq(state, kind="gap", gap_id=gap["id"], channel="telegram")
            return (
                "В материалах сайта нет ответа на этот вопрос.\nОбратитесь в поддержку: "
                + link("/support", "открыть обращение")
                + "."
            )
    found = search(query, programs)
    if found["reason"] not in ("none", "empty"):
        strong = [
            program
            for program in found["programs"]
            if any(
                any(same_stem(word, title) for title in tokenize(program["title"]))
                for word in parse_query(query)["stems"]
            )
        ]
        selected = strong or found["programs"]
        intro = (
            "Подходят по вашим условиям:"
            if found["reason"] == "filter"
            else "Подходит одна программа:"
            if len(strong) == 1
            else "Подходящие программы:"
            if strong
            else "Точного совпадения нет, похожие программы:"
        )
        lines = [intro, ""]
        for program in selected[:5]:
            meta = [
                program["formatLabel"] or program["format"],
                f"{thousands(program['price'])} ₽" if program["price"] is not None else None,
                program["start"],
            ]
            lines.extend(
                [
                    "▸ <b>" + html.escape(program["title"]) + "</b>",
                    "  " + html.escape(" · ".join(value for value in meta if value)),
                    "  " + link(program["url"], "Открыть"),
                    "",
                ]
            )
        return "\n".join(lines).strip()
    await log_faq(state, kind="none", channel="telegram")
    return (
        "Ответ не найден. Уточните вопрос или обратитесь в поддержку.\n🌐 "
        + link("/support", "Поддержка на сайте")
        + "\n🌐 "
        + link("/dpo", "Программы ДПО")
    )
