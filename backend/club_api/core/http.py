import json
import logging
import re
from types import SimpleNamespace

from club_api.core.errors import ApiError
from club_api.core.security import RateLimits, client_ip

logger = logging.getLogger("club.http")
JSON_BODY_LIMIT = 256 * 1024
UPLOAD_BODY_LIMIT = 129 * 1024 * 1024
AVATAR_BODY_LIMIT = 4 * 1024 * 1024
UNCACHED_PATHS = ("/ready", "/portfolio/challenge")
HEADERS = {
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "strict-transport-security": "max-age=15552000; includeSubDomains",
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "x-dns-prefetch-control": "off",
    "x-download-options": "noopen",
    "x-permitted-cross-domain-policies": "none",
    "x-xss-protection": "0",
}


UNLIMITED_PATHS = ("/health", "/ready")
UNCACHED_PREFIXES = ("/me", "/admin", "/auth", "/podcasts", "/orders", "/support")
DEFAULT_WINDOW_SECONDS = 60
TOO_MANY = "Слишком много запросов. Попробуйте позже."
TOO_LARGE = "Запрос слишком большой."


def secured_headers(path, headers):
    secured = dict(headers)
    for key, value in HEADERS.items():
        secured.setdefault(key.encode(), value.encode())
    if path.startswith(UNCACHED_PREFIXES) or path in UNCACHED_PATHS:
        secured[b"cache-control"] = b"no-store"
    return list(secured.items())


def route_expression(pattern, method):
    if isinstance(pattern, tuple):
        if pattern[0] != method:
            return None
        pattern = pattern[1]
    return pattern, re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(pattern))


def body_limit(method, path):
    if method == "POST" and path == "/admin/media":
        return UPLOAD_BODY_LIMIT
    if method == "POST" and path == "/me/avatar":
        return AVATAR_BODY_LIMIT
    return JSON_BODY_LIMIT


def declared_size(headers):
    try:
        size = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    return size if size >= 0 else None


def replayed(body, receive):
    sent = False

    async def buffered_receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return buffered_receive


async def buffered(receive, maximum):
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > maximum:
            raise ApiError(413, TOO_LARGE)
        chunks.append(chunk)
        if not message.get("more_body"):
            return replayed(b"".join(chunks), receive)


def bounded(receive, maximum):
    size = 0

    async def bounded_receive():
        nonlocal size
        message = await receive()
        size += len(message.get("body", b""))
        if size > maximum:
            raise ApiError(413, "Файл слишком большой.")
        return message

    return bounded_receive


class Exchange:
    def __init__(self, send, path):
        self.send, self.path = send, path
        self.status, self.started = None, False

    async def secure_send(self, message):
        if message["type"] == "http.response.start":
            self.started = True
            self.status = message["status"]
            message["headers"] = secured_headers(self.path, message["headers"])
        await self.send(message)

    async def error(self, code, text):
        body = json.dumps({"error": text}, ensure_ascii=False).encode()
        await self.secure_send(
            {
                "type": "http.response.start",
                "status": code,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await self.secure_send({"type": "http.response.body", "body": body})


class SecurityMiddleware:
    def __init__(self, app, settings, route_limits):
        self.app, self.settings, self.route_limits = app, settings, route_limits
        self.limits = RateLimits()

    def limited(self, ip, method, path):
        if path in UNLIMITED_PATHS:
            return False
        if not self.limits.check((ip, "global"), self.settings.RATE_LIMIT_MAX):
            return True
        for pattern, policy in self.route_limits.items():
            route = route_expression(pattern, method)
            if route and re.fullmatch(route[1], path):
                maximum, window = policy if isinstance(policy, tuple) else (policy, DEFAULT_WINDOW_SECONDS)
                return not self.limits.check((ip, method, route[0]), maximum, window)
        return False

    async def receiver(self, receive, request, exchange):
        maximum = body_limit(request.method, request.path)
        size = declared_size(request.headers)
        if size is None:
            await exchange.error(400, "Некорректный запрос.")
            return None
        if size > maximum:
            await exchange.error(413, TOO_LARGE)
            return None
        if maximum != JSON_BODY_LIMIT:
            return bounded(receive, maximum)
        try:
            return await buffered(receive, maximum)
        except ApiError as exception:
            await exchange.error(exception.status, exception.message)
            return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        request = SimpleNamespace(
            META={"REMOTE_ADDR": scope.get("client", ("", 0))[0]},
            headers={key.decode().lower(): value.decode("latin1") for key, value in scope["headers"]},
            method=method,
            path=path,
        )
        exchange = Exchange(send, path)
        if self.limited(client_ip(request), method, path):
            return await exchange.error(429, TOO_MANY)
        actual_receive = await self.receiver(receive, request, exchange)
        if actual_receive is not None:
            await self.serve(scope, actual_receive, exchange, method, path)

    async def serve(self, scope, receive, exchange, method, path):
        try:
            await self.app(scope, receive, exchange.secure_send)
        except ApiError as exception:
            if exchange.started:
                raise RuntimeError("Ошибка после начала ответа") from None
            await exchange.error(exception.status, exception.message)
        except Exception:
            from club_api.observability.errors import capture

            capture("request_failed")
            logger.error("Не удалось обработать запрос")
            if exchange.started:
                raise RuntimeError("Ошибка после начала ответа") from None
            await exchange.error(500, "Внутренняя ошибка.")
        finally:
            logger.info("%s %s %s", method, path, exchange.status if exchange.started else "disconnected")
