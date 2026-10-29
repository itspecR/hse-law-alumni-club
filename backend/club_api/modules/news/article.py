import re
from datetime import UTC, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from club_api.db.store import normalize

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
SKIPPED = {"script", "style", "noscript", "iframe", "form", "button", "svg", "video", "audio"}
HEADINGS = {"h1": "## ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "#### ", "h6": "#### "}
LISTS = {"ul", "ol"}
MARKS = {"strong": "**", "b": "**", "em": "*", "i": "*"}
REMOVED = ".annotation, .lead-in, .head-picture, .articleMeta, .tag-set, .post-meta, .print, .share"
ESCAPED = re.compile(r"([\\`*_\[\]<>])")
LINE_START = re.compile(r"^(\s*)([#>+-]|\d+[.)])")
NOON = 12


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def escape(text):
    return ESCAPED.sub(r"\\\1", text)


def escape_start(line):
    return LINE_START.sub(lambda match: match[1] + "\\" + match[2], line)


def absolute(base, value):
    url = urljoin(base, value or "")
    return url if url.startswith("https://") else None


def link(node, base, text):
    href = absolute(base, node.get("href"))
    return f"[{text}]({href})" if href and text and not re.search(r"[\s()]", href) else text


def inline(node, base):
    parts = []
    for child in node.children:
        if isinstance(child, NavigableString):
            parts.append(escape(re.sub(r"\s+", " ", str(child))))
        elif child.name == "br":
            parts.append("\n")
        elif child.name in SKIPPED or child.name == "img":
            continue
        else:
            parts.append(wrapped(child, inline(child, base), base))
    return "".join(parts)


def wrapped(node, raw, base):
    match = re.fullmatch(r"(\s*)(.*?)(\s*)", raw, re.S)
    before, text, after = match.groups()
    if not text:
        return raw
    if node.name in MARKS:
        return before + MARKS[node.name] + text + MARKS[node.name] + after
    if node.name == "a":
        return before + link(node, base, text) + after
    return raw


def paragraph(text, prefix=""):
    lines = [escape_start(re.sub(" {2,}", " ", line.strip())) for line in text.split("\n") if line.strip()]
    return prefix + "\n".join(lines) if lines else None


def images(node, base):
    found = [node] if node.name == "img" else node.find_all("img")
    return [("image", url, clean(image.get("alt"))) for image in found if (url := absolute(base, image.get("src")))]


def list_block(node, base):
    items = node.find_all("li", recursive=False)
    marks = [f"{index}. " if node.name == "ol" else "- " for index in range(1, len(items) + 1)]
    lines = [mark + clean(inline(item, base)) for mark, item in zip(marks, items, strict=True)]
    return "\n".join(line for line in lines if line.strip(" -.0123456789"))


def table_block(node, base):
    rows = [" · ".join(clean(inline(cell, base)) for cell in row.find_all(["td", "th"])) for row in node.find_all("tr")]
    return "\n\n".join(row for row in rows if row)


def blocks(node, base):
    for child in node.children:
        if isinstance(child, NavigableString):
            text = paragraph(escape(clean(str(child))))
            if text:
                yield text
            continue
        if child.name in SKIPPED:
            continue
        if child.name == "img":
            yield from images(child, base)
        elif child.name in HEADINGS:
            text = paragraph(clean(inline(child, base)), HEADINGS[child.name])
            if text:
                yield text
        elif child.name == "p":
            text = paragraph(inline(child, base).strip())
            if text:
                yield text
            yield from images(child, base)
        elif child.name in LISTS:
            text = list_block(child, base)
            if text:
                yield text
        elif child.name == "blockquote":
            text = paragraph(clean(inline(child, base)), "> ")
            if text:
                yield text
        elif child.name == "table":
            text = table_block(child, base)
            if text:
                yield text
        elif isinstance(child, Tag):
            yield from blocks(child, base)


def russian_date(value):
    match = re.search(r"([0-9]{1,2})\s+(" + "|".join(MONTHS) + r"),?\s+([0-9]{4})", value or "")
    if not match:
        return None
    try:
        return normalize(datetime(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1]), NOON, tzinfo=UTC))
    except ValueError:
        return None


def article_date(document):
    for item in document.select(".articleMetaItem"):
        if item.select_one(".articleMetaItem__label--date"):
            return russian_date(item.get_text(" "))
    return None


def parse_article(html, url):
    document = BeautifulSoup(html, "html.parser")
    text = document.select_one(".post__text")
    if not text:
        return None
    lead = text.select_one(".lead-in")
    cover = text.select_one(".head-picture img")
    result = {
        "published_at": article_date(document),
        "excerpt": clean(lead.get_text(" ")) if lead else None,
        "cover": absolute(url, cover.get("src")) if cover else None,
    }
    for node in text.select(REMOVED):
        node.decompose()
    return {**result, "blocks": list(blocks(text, url))}
