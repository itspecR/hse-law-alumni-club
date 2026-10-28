from urllib.parse import urljoin, urlsplit

from club_api.core.errors import ApiError


async def get_html(state, url, *, allowed_hosts, max_bytes=2000000):
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
            "GET", url, timeout=15, headers={"User-Agent": "HSEAlumniClub/1.0", "Accept": "text/html"}
        ) as response:
            if response.is_redirect and response.headers.get("location"):
                url = urljoin(url, response.headers["location"])
                continue
            if not response.is_success or "text/html" not in response.headers.get("content-type", ""):
                raise ApiError(502, "Источник временно недоступен.")
            length = response.headers.get("content-length")
            if length and (not length.isascii() or not length.isdigit() or len(length) > 12 or int(length) > max_bytes):
                raise ApiError(502, "Ответ источника слишком большой.")
            parts, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(502, "Ответ источника слишком большой.")
                parts.append(chunk)
            return b"".join(parts).decode("utf-8", errors="replace")
    raise ApiError(502, "Источник временно недоступен.")
