import asyncio
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from bs4 import BeautifulSoup

from club_api.core.errors import ApiError
from club_api.core.models import parse_date
from club_api.core.outgoing import get_html
from club_api.db.queries import Query, acquire_lock, release_lock
from club_api.db.store import normalize
from club_api.modules.catalog.sync import MONTHS
from club_api.modules.checkout.store import digest

logger = logging.getLogger("club.news")
SOURCES = [
    {"id": "alumni", "title": "Выпускники ВШЭ", "url": "https://pravo.hse.ru/businessandlaw/alumni"},
    {"id": "career", "title": "Карьера и работодатели", "url": "https://pravo.hse.ru/businessandlaw/career"},
    {"id": "telegram", "title": "Telegram клуба", "url": "https://t.me/s/AlumniLawHSE"},
]


def clean(value):
    return re.sub(r"\s+", " ", value).replace("\u2014", "–").strip()


def canonical_news_url(value):
    value = re.split(r"[?#]", value)[0]
    if re.fullmatch(r"https://pravo\.hse\.ru/news/[0-9]+\.html", value):
        return value
    match = re.fullmatch(r"https://t\.me/(?:s/)?AlumniLawHSE/([0-9]+)", value, re.I)
    return "https://t.me/AlumniLawHSE/" + match[1] if match else None


def valid_date(value):
    try:
        return normalize(parse_date(value)) if value else None
    except ValueError, TypeError, AttributeError:
        return None


def parse_source(html, source):
    document = BeautifulSoup(html, "html.parser")
    for tag in document.select("script,style"):
        tag.decompose()
    items = {}
    if source == "telegram":
        for node in document.select(".tgme_widget_message[data-post]"):
            body = node.select_one(".tgme_widget_message_text")
            if not body:
                continue
            for tag in body.select("br"):
                tag.replace_with("\n")
            lines = body.get_text().split("\n")
            title = clean(next((line for line in lines if len(clean(line)) >= 8), body.get_text()))[:180]
            own = canonical_news_url("https://t.me/" + node.get("data-post", ""))
            linked = next(
                (
                    url
                    for anchor in body.select("a[href]")
                    if (url := canonical_news_url(anchor["href"])) and url.startswith("https://pravo.hse.ru/")
                ),
                None,
            )
            date = node.select_one("time[datetime]")
            if own and len(title) >= 8:
                url = linked or own
                items[url] = {
                    "source_url": url,
                    "title": title,
                    "published_at": valid_date(date["datetime"] if date else None),
                }
    else:
        for anchor in document.select(".plate_news__title a[href]"):
            url, title = canonical_news_url(anchor["href"]), clean(anchor.get_text())
            if url and title:
                items[url] = {"source_url": url, "title": title[:240], "published_at": None}
    return list(items.values())[:60]


def parse_russian_date(value):
    match = re.search(r"([0-9]{1,2})\s+(" + "|".join(MONTHS) + r")\s+([0-9]{4})", value)
    if not match:
        return None
    try:
        return normalize(datetime(int(match[3]), MONTHS.index(match[2]) + 1, int(match[1]), 12, tzinfo=UTC))
    except ValueError:
        return None


def article_date(html):
    document = BeautifulSoup(html, "html.parser")
    for selector, attribute in (
        ('meta[property="article:published_time"]', "content"),
        ('meta[itemprop="datePublished"]', "content"),
        ('[itemprop="datePublished"]', "datetime"),
    ):
        node = document.select_one(selector)
        if node and (date := valid_date(node.get(attribute))):
            return date
    for node in document.select(".articleMetaItem"):
        if node.select_one(".articleMetaItem__label--date"):
            value = node.select_one(".articleMetaItem__content")
            if value:
                return parse_russian_date(value.get_text())
    return None


async def refresh_source(state, source):
    config = next((row for row in SOURCES if row["id"] == source), None)
    if not config:
        raise ApiError(400, "Неизвестный источник.")
    async with state.database.connection() as connection:
        lock_key = "news-source:" + source
        if not await acquire_lock(connection, lock_key, wait=0):
            return {"busy": True, "found": 0}
        try:
            recent = await (
                await connection.execute(
                    Query(
                        "SELECT source FROM club_news_source_runs WHERE source=%s AND checked_at>now()-interval '1 minute'",
                        "SELECT source FROM club_news_source_runs WHERE source=%s AND checked_at>now()-INTERVAL 1 MINUTE",
                    ),
                    (source,),
                )
            ).fetchone()
            if recent:
                return {"busy": True, "found": 0}
            try:
                html = await get_html(state, config["url"], allowed_hosts={"pravo.hse.ru", "t.me"})
                items = await asyncio.to_thread(parse_source, html, source)
                if not items:
                    raise ApiError(502, "Публикации не найдены.")
                for item in items:
                    if source != "telegram":
                        try:
                            page = await get_html(state, item["source_url"], allowed_hosts={"pravo.hse.ru", "t.me"})
                            item["published_at"] = await asyncio.to_thread(article_date, page)
                        except Exception:
                            logger.warning("Дата публикации не получена")
                    if state.database.vendor == "mysql":
                        from club_api.modules.news.storage import merge_candidate

                        await connection.run(merge_candidate, source, item)
                    else:
                        await connection.execute(
                            "INSERT INTO club_news_inbox(id,source_url,sources,title,published_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(source_url) DO UPDATE SET sources=ARRAY(SELECT DISTINCT unnest(club_news_inbox.sources||EXCLUDED.sources)),published_at=COALESCE(club_news_inbox.published_at,EXCLUDED.published_at)",
                            (
                                digest(item["source_url"]),
                                item["source_url"],
                                [source],
                                item["title"],
                                item["published_at"],
                            ),
                        )
                await connection.execute(
                    Query(
                        "UPDATE club_news_inbox i SET state='imported',news_id=n.id FROM news n WHERE n.source_url=i.source_url AND i.state='new'",
                        "UPDATE club_news_inbox i JOIN news n ON n.source_url=i.source_url SET i.state='imported',i.news_id=n.id WHERE i.state='new'",
                    )
                )
                await connection.execute(
                    Query(
                        "INSERT INTO club_news_source_runs(source,found) VALUES(%s,%s) ON CONFLICT(source) DO UPDATE SET checked_at=now(),found=EXCLUDED.found,error=NULL",
                        "INSERT INTO club_news_source_runs(source,found) VALUES(%s,%s) ON DUPLICATE KEY UPDATE checked_at=now(),found=VALUES(found),error=NULL",
                    ),
                    (source, len(items)),
                )
                return {"found": len(items), "busy": False}
            except Exception:
                await connection.execute(
                    Query(
                        "INSERT INTO club_news_source_runs(source,error) VALUES(%s,%s) ON CONFLICT(source) DO UPDATE SET checked_at=now(),error=EXCLUDED.error",
                        "INSERT INTO club_news_source_runs(source,error) VALUES(%s,%s) ON DUPLICATE KEY UPDATE checked_at=now(),error=VALUES(error)",
                    ),
                    (source, "Не удалось обновить источник. Повторите позже."),
                )
                raise ApiError(502, "Не удалось обновить источник. Сохраненные материалы доступны.") from None
        finally:
            await release_lock(connection, lock_key)


async def import_candidate(state, id, title, excerpt):
    async with state.database.transaction() as connection:
        item = await (
            await connection.execute("SELECT * FROM club_news_inbox WHERE id=%s FOR UPDATE", (id,))
        ).fetchone()
        if not item:
            raise ApiError(404, "Материал не найден.")
        previous = await (
            await connection.execute("SELECT id FROM news WHERE source_url=%s LIMIT 1", (item["source_url"],))
        ).fetchone()
        news_id = str(previous["id"]) if previous else str(uuid4())
        if not previous:
            await state.store.create(
                "news",
                {
                    "id": news_id,
                    "slug": "source-" + id[:24],
                    "title": title,
                    "excerpt": excerpt or None,
                    "body": excerpt or None,
                    "source_url": item["source_url"],
                    "published_at": item["published_at"],
                    "status": "draft",
                },
                connection=connection,
            )
        await connection.execute("UPDATE club_news_inbox SET state='imported',news_id=%s WHERE id=%s", (news_id, id))
        return {"ok": True, "id": news_id}
