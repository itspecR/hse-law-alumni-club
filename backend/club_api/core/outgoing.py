import asyncio
import json
import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from club_api.core.errors import ApiError

UNAVAILABLE = "Источник временно недоступен."
HTML = "text/html"
JSON = "application/json"
IMAGE = "image/*"


class Pacer:
    def __init__(self, delay):
        self.delay, self.last = delay, None

    async def wait(self):
        if self.last is not None:
            pause = self.delay - (time.monotonic() - self.last)
            if pause > 0:
                await asyncio.sleep(pause)
        self.last = time.monotonic()


def image_name(url):
    return PurePosixPath(unquote(urlsplit(url).path)).name or "image"


async def get_html(state, url, *, allowed_hosts, max_bytes=2000000):
    body = await get_bytes(state, url, allowed_hosts=allowed_hosts, max_bytes=max_bytes, accept=HTML)
    return body.decode("utf-8", errors="replace")


async def get_json(state, url, *, allowed_hosts, max_bytes):
    body = await get_bytes(state, url, allowed_hosts=allowed_hosts, max_bytes=max_bytes, accept=JSON)
    try:
        return json.loads(body)
    except ValueError:
        raise ApiError(502, "Источник вернул некорректный ответ.") from None


async def get_bytes(state, url, *, allowed_hosts, max_bytes, accept):
    try:
        return await fetch(state, url, allowed_hosts, max_bytes, accept)
    except httpx.HTTPError:
        raise ApiError(502, UNAVAILABLE) from None


async def fetch(state, url, allowed_hosts, max_bytes, accept):
    for _ in range(4):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in allowed_hosts
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise ApiError(502, "Источник вернул неподходящий адрес.")
        async with state.client.stream(
            "GET", url, timeout=15, headers={"User-Agent": "HSEAlumniClub/1.0", "Accept": accept}
        ) as response:
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                continue
            kind = response.headers.get("content-type", "")
            if not response.is_success or not kind.startswith(accept.removesuffix("*")):
                raise ApiError(502, UNAVAILABLE)
            length = response.headers.get("content-length")
            if length and (not length.isascii() or not length.isdigit() or len(length) > 12 or int(length) > max_bytes):
                raise ApiError(502, "Ответ источника слишком большой.")
            parts, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(502, "Ответ источника слишком большой.")
                parts.append(chunk)
            return b"".join(parts)
    raise ApiError(502, UNAVAILABLE)
