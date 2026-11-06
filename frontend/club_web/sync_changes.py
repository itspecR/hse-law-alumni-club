import argparse
import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import httpx

from club_web.changes import CHANNEL, parse_changes, source_url, valid_instant

FEED = f"https://t.me/s/{CHANNEL}"
MAX_BYTES = 2_000_000
MAX_PAGES = 10
IGNORED = {"script", "style", "iframe", "object", "svg"}
VOID = {"br", "img", "hr", "input", "link", "meta", "source", "wbr", "area", "base", "col", "embed", "param", "track"}


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1]["children"].append(data)


def descendants(node):
    for child in node["children"]:
        if isinstance(child, dict):
            yield child
            yield from descendants(child)


def has_class(node, name):
    return name in (node["attrs"].get("class") or "").split()


def parse_blocks(content):
    blocks, segments, all_bold = [], [], True

    def flush():
        nonlocal segments, all_bold
        text = "".join(segment["text"] for segment in segments)
        if text.strip():
            blocks.append(
                {
                    "heading": all_bold and len(text) < 180 or text.strip().endswith(":") and len(text) < 80,
                    "segments": segments,
                }
            )
        segments, all_bold = [], True

    def walk(node, bold=False, href=None):
        nonlocal all_bold
        if isinstance(node, str):
            text = node.replace("\u00a0", " ").replace("—", "–")
            if text:
                segments.append({"text": text, **({"url": href} if href else {})})
            if text.strip() and not bold:
                all_bold = False
            return
        tag = node["tag"]
        if tag in IGNORED:
            return
        if tag == "br":
            flush()
            return
        if tag in {"p", "div", "li"}:
            flush()
        if tag == "a":
            href = source_url(
                re.sub(
                    r"^http://publication\.pravo\.gov\.ru/",
                    "https://publication.pravo.gov.ru/",
                    node["attrs"].get("href") or "",
                )
            )
        for child in node["children"]:
            walk(child, bold or tag in {"b", "strong"}, href)
        if tag in {"p", "div", "li"}:
            flush()

    for child in content["children"]:
        walk(child)
    flush()
    return blocks


def parse_feed(html):
    if len(html.encode()) > MAX_BYTES:
        raise ValueError("source_too_large")
    document = Document()
    document.feed(html)
    messages = [
        node
        for node in descendants(document.root)
        if has_class(node, "tgme_widget_message") and "data-post" in node["attrs"]
    ]
    if not messages:
        raise ValueError("source_markup_missing")
    posts, visible = [], []
    for message in messages:
        match = re.fullmatch(rf"{CHANNEL}/([1-9]\d{{0,11}})", message["attrs"]["data-post"] or "")
        if not match or int(match[1]) in visible:
            raise ValueError("unexpected_or_duplicate_post")
        post_id = int(match[1])
        visible.append(post_id)
        content = next((node for node in descendants(message) if has_class(node, "tgme_widget_message_text")), None)
        if not content:
            continue
        blocks = parse_blocks(content)
        plain = "\n".join("".join(segment["text"] for segment in block["segments"]) for block in blocks)
        if len(plain) < 80 or re.match(r"^(Channel created|Проверка связи)(?:\n|$)", plain.strip(), re.I):
            continue
        date_node = next(
            (node for node in descendants(message) if node["tag"] == "time" and "datetime" in node["attrs"]), None
        )
        instant = date_node["attrs"]["datetime"] if date_node else None
        if not valid_instant(instant):
            raise ValueError("post_date_missing")
        published_at = (
            datetime.fromisoformat(instant.replace("Z", "+00:00"))
            .astimezone(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        published = published_at[:10]
        first = "".join(segment["text"] for segment in blocks[0]["segments"]).strip()
        title = first if len(first) <= 350 else f"Материал LegisDigest от {published}"
        body = blocks[1:] if len(first) <= 350 and len(blocks) > 1 else blocks
        paragraph = next(
            (
                "".join(segment["text"] for segment in block["segments"]).strip()
                for block in body
                if not block["heading"]
            ),
            first,
        )
        summary = re.sub(r"\s+\S*$", "", paragraph[:277]) + "…" if len(paragraph) > 280 else paragraph
        kind = (
            "Аудиовыпуск"
            if re.search("аудиовыпуск", title, re.I)
            else "Обзор недели"
            if re.search("обзор законодательства|готовится|вступило", title, re.I)
            else "Краткая справка"
        )
        posts.append(
            {
                "id": f"tg-{post_id}",
                "title": title,
                "kind": kind,
                "number": "",
                "date": published,
                "published": published,
                "url": f"https://t.me/{CHANNEL}/{post_id}",
                "topic": kind,
                "effectiveDate": None,
                "entryType": "digest",
                "summary": summary,
                "blocks": body,
                "sourcePublishedAt": published_at,
            }
        )
    more = next(
        (
            node
            for node in descendants(document.root)
            if node["tag"] == "a" and has_class(node, "tme_messages_more") and "data-before" in node["attrs"]
        ),
        None,
    )
    before = more["attrs"]["data-before"] if more else None
    if before and not re.fullmatch(r"[1-9]\d{0,11}", before):
        raise ValueError("invalid_pagination")
    return {"posts": posts, "visibleIds": visible, "nextBefore": int(before) if before else None}


async def fetch_text(url):
    if not re.fullmatch(r"https://t\.me/s/LegisDigest(?:\?before=[1-9]\d{0,11})?", url):
        raise ValueError("unexpected_fetch_url")
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        async with client.stream("GET", url) as response:
            if not response.is_success:
                raise ValueError("source_http_error")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("source_too_large")
            return body.decode("utf-8")


async def synchronize(previous, now, get=fetch_text):
    if not valid_instant(now):
        raise ValueError("invalid_clock")
    previous = parse_changes(previous)
    try:
        found, url = {}, FEED
        for _ in range(MAX_PAGES):
            result = parse_feed(await get(url))
            for post in result["posts"]:
                if post["id"] in found or datetime.fromisoformat(post["sourcePublishedAt"]) > datetime.fromisoformat(
                    now
                ) + timedelta(minutes=5):
                    raise ValueError("duplicate_or_future_post")
                found[post["id"]] = post
            before = result["nextBefore"]
            if not before:
                break
            if before > min(result["visibleIds"]) or f"{FEED}?before={before}" == url:
                raise ValueError("pagination_not_older")
            url = f"{FEED}?before={before}"
        if not found:
            raise ValueError("no_materials")
        latest = max(post["sourcePublishedAt"] for post in found.values())
        if previous.get("lastPostAt") and datetime.fromisoformat(latest) < datetime.fromisoformat(
            previous["lastPostAt"]
        ):
            raise ValueError("source_went_backwards")
        items = {item["id"]: item for item in previous["items"]} | found
        dates = [item["published"] for item in items.values()]
        return parse_changes(
            {
                "version": 2,
                "mode": "channel",
                "periodFrom": min(dates),
                "periodTo": max(dates),
                "checkedAt": now,
                "lastSuccessAt": now,
                "lastPostAt": latest,
                "syncStatus": "ok",
                "items": list(items.values()),
            }
        )
    except ValueError, KeyError, TypeError, httpx.HTTPError:
        return parse_changes({**previous, "checkedAt": now, "syncStatus": "unavailable"})


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    previous = parse_changes(json.loads(args.output.read_text(encoding="utf-8")))
    result = await synchronize(previous, datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"))
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "status": result["syncStatus"],
                "materials": sum(item["entryType"] == "digest" for item in result["items"]),
                "lastPostAt": result.get("lastPostAt"),
                "lastSuccessAt": result.get("lastSuccessAt"),
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
