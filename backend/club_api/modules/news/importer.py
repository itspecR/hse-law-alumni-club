import asyncio
import io
import logging
import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from club_api.core.errors import ApiError
from club_api.core.models import parse_date
from club_api.core.outgoing import IMAGE, get_bytes, get_html
from club_api.db.queries import Query, acquire_lock, release_lock
from club_api.modules.checkout.store import digest
from club_api.modules.content.admin import write_content
from club_api.modules.media.service import MAX_IMAGE_BYTES
from club_api.modules.news.article import escape, parse_article
from club_api.modules.news.sources import SOURCES, parse_source

logger = logging.getLogger("club.news")
CRAWL_DELAY_SECONDS = 3
MAX_IMPORTS_PER_RUN = 30
PAGE_HOSTS = {"pravo.hse.ru"}
IMAGE_HOSTS = {"pravo.hse.ru", "www.hse.ru"}
SYNC_LOCK = "news-sync"
MEDIA_KIND = "news"
FAILED = "Не удалось обновить источник. Повторите позже."
FAILURE_ALERT = 3


class Pacer:
    def __init__(self, delay):
        self.delay, self.last = delay, None

    async def wait(self):
        if self.last is not None:
            pause = self.delay - (time.monotonic() - self.last)
            if pause > 0:
                await asyncio.sleep(pause)
        self.last = time.monotonic()


async def sync_news(state, *, delay=CRAWL_DELAY_SECONDS):
    async with state.database.connection() as connection:
        if not await acquire_lock(connection, SYNC_LOCK, wait=0):
            return {"busy": True, "sources": []}
        try:
            pacer = Pacer(delay)
            return {"busy": False, "sources": [await sync_source(state, pacer, source) for source in SOURCES]}
        finally:
            await release_lock(connection, SYNC_LOCK)


async def sync_source(state, pacer, source):
    try:
        await pacer.wait()
        html = await get_html(state, source["url"], allowed_hosts=PAGE_HOSTS)
        items = await asyncio.to_thread(parse_source, html, source["url"])
        if not items:
            raise ApiError(502, "Публикации не найдены.")
        known = await known_urls(state)
        fresh = [item for item in items if item["source_url"] not in known][:MAX_IMPORTS_PER_RUN]
        added = sum([await import_item(state, pacer, source["id"], item) for item in fresh])
        await record_success(state, source["id"], len(items), added)
        return {"id": source["id"], "found": len(items), "added": added}
    except Exception as error:
        reason = failure(error)
        logger.warning("Источник новостей не обновлен: %s", reason, extra={"source": source["id"]})
        if await record_failure(state, source["id"], reason) == FAILURE_ALERT:
            await state.notifications.office_text(alert(source, reason))
        return {"id": source["id"], "error": reason}


def alert(source, reason):
    return (
        f"Источник новостей «{source['title']}» не отвечает {FAILURE_ALERT} проверки подряд: {reason} "
        "Новости с него не загружаются. Подробности в панели управления, раздел «Источники новостей»."
    )


def failure(error):
    return error.message if isinstance(error, ApiError) else FAILED


async def known_urls(state):
    rows = await state.database.rows(
        "SELECT source_url FROM club_news_inbox WHERE state<>'new' "
        "UNION SELECT source_url FROM news WHERE source_url IS NOT NULL"
    )
    return {row["source_url"] for row in rows}


async def import_item(state, pacer, source, item):
    saved = []
    try:
        await pacer.wait()
        html = await get_html(state, item["source_url"], allowed_hosts=PAGE_HOSTS)
        article = await asyncio.to_thread(parse_article, html, item["source_url"])
        if not article:
            raise ApiError(502, "Текст новости не найден.")
        cover = await save_image(state, pacer, article["cover"], saved)
        body = await render_body(state, pacer, article["blocks"], saved)
        await publish(state, source, item, {**article, "cover": cover, "body": body})
        return True
    except Exception as error:
        logger.warning("Новость источника не загружена: %s", failure(error), extra={"source": source})
        for id in saved:
            await state.media.delete(id)
        await remember(state.database, source, item, "new", None, None)
        return False


async def save_image(state, pacer, url, saved):
    if not url:
        return None
    try:
        await pacer.wait()
        data = await get_bytes(state, url, allowed_hosts=IMAGE_HOSTS, max_bytes=MAX_IMAGE_BYTES, accept=IMAGE)
        file = await state.media.save(io.BytesIO(data), filename=image_name(url), kind=MEDIA_KIND)
    except ApiError:
        logger.warning("Изображение новости не загружено")
        return None
    saved.append(file["id"])
    return file["id"]


def image_name(url):
    return PurePosixPath(unquote(urlsplit(url).path)).name or "image"


async def render_body(state, pacer, blocks, saved):
    parts = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
        elif id := await save_image(state, pacer, block[1], saved):
            parts.append(f"![{escape(block[2])}]({id})")
    return "\n\n".join(parts)


async def publish(state, source, item, article):
    async with state.database.transaction() as connection:
        row = await write_content(
            state,
            "news",
            {
                "title": item["title"],
                "excerpt": article["excerpt"],
                "body": article["body"],
                "cover": article["cover"],
                "source_url": item["source_url"],
                "origin": "faculty",
                "status": "published",
                "published_at": article["published_at"],
            },
            connection=connection,
        )
        moment = parse_date(article["published_at"]) if article["published_at"] else None
        await remember(connection, source, item, "imported", row["id"], moment)


async def remember(target, source, item, state, news_id, published_at):
    await target.execute(
        Query(
            "INSERT INTO club_news_inbox(id,source_url,sources,title,state,news_id,published_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_url) DO UPDATE SET state=EXCLUDED.state,"
            "news_id=EXCLUDED.news_id,title=EXCLUDED.title,published_at=EXCLUDED.published_at",
            "INSERT INTO club_news_inbox(id,source_url,sources,title,state,news_id,published_at) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE state=VALUES(state),"
            "news_id=VALUES(news_id),title=VALUES(title),published_at=VALUES(published_at)",
        ),
        (digest(item["source_url"]), item["source_url"], [source], item["title"], state, news_id, published_at),
    )


async def record_success(state, source, found, added):
    await state.database.execute(
        Query(
            "INSERT INTO club_news_source_runs(source,found,added) VALUES(%s,%s,%s) ON CONFLICT(source) DO UPDATE "
            "SET checked_at=now(),found=EXCLUDED.found,added=EXCLUDED.added,error=NULL,failures=0",
            "INSERT INTO club_news_source_runs(source,found,added) VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE "
            "checked_at=now(),found=VALUES(found),added=VALUES(added),error=NULL,failures=0",
        ),
        (source, found, added),
    )


async def record_failure(state, source, reason):
    await state.database.execute(
        Query(
            "INSERT INTO club_news_source_runs(source,error,failures) VALUES(%s,%s,1) ON CONFLICT(source) DO UPDATE "
            "SET checked_at=now(),error=EXCLUDED.error,failures=club_news_source_runs.failures+1",
            "INSERT INTO club_news_source_runs(source,error,failures) VALUES(%s,%s,1) ON DUPLICATE KEY UPDATE "
            "checked_at=now(),error=VALUES(error),failures=failures+1",
        ),
        (source, reason),
    )
    rows = await state.database.rows("SELECT failures FROM club_news_source_runs WHERE source=%s", (source,))
    return rows[0]["failures"]
