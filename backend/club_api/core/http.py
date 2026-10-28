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


class SecurityMiddleware:
    def __init__(self, app, settings, route_limits):
        self.app, self.settings, self.route_limits = app, settings, route_limits
        self.limits = RateLimits()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = SimpleNamespace(
            META={"REMOTE_ADDR": scope.get("client", ("", 0))[0]},
            headers={key.decode().lower(): value.decode("latin1") for key, value in scope["headers"]},
        )
        path, method = scope["path"], scope["method"]
        ip = client_ip(request)
        status = None
        response_started = False

        async def secure_send(message):
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = dict(message["headers"])
                for key, value in HEADERS.items():
                    headers.setdefault(key.encode(), value.encode())
                if (
                    path.startswith(("/me", "/admin", "/auth", "/podcasts", "/orders", "/support"))
                    or path in UNCACHED_PATHS
                ):
                    headers[b"cache-control"] = b"no-store"
                message["headers"] = list(headers.items())
            await send(message)

        async def error(code, text):
            body = json.dumps({"error": text}, ensure_ascii=False).encode()
            await secure_send(
                {
                    "type": "http.response.start",
                    "status": code,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await secure_send({"type": "http.response.body", "body": body})

        if path not in ("/health", "/ready"):
            if not self.limits.check((ip, "global"), self.settings.RATE_LIMIT_MAX):
                return await error(429, "Слишком много запросов. Попробуйте позже.")
            for pattern, policy in self.route_limits.items():
                if isinstance(pattern, tuple):
                    if pattern[0] != method:
                        continue
                    pattern = pattern[1]
                expression = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(pattern))
                if re.fullmatch(expression, path):
                    maximum, window = policy if isinstance(policy, tuple) else (policy, 60)
                    if not self.limits.check((ip, method, pattern), maximum, window):
                        return await error(429, "Слишком много запросов. Попробуйте позже.")
                    break
        maximum = (
            UPLOAD_BODY_LIMIT
            if method == "POST" and path == "/admin/media"
            else AVATAR_BODY_LIMIT
            if method == "POST" and path == "/me/avatar"
            else JSON_BODY_LIMIT
        )
        try:
            size = int(request.headers.get("content-length", "0"))
            if size < 0:
                raise ValueError
        except ValueError:
            return await error(400, "Некорректный запрос.")
        if size > maximum:
            return await error(413, "Запрос слишком большой.")
        if maximum == JSON_BODY_LIMIT:
            chunks, size = [], 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > maximum:
                    return await error(413, "Запрос слишком большой.")
                chunks.append(chunk)
                if not message.get("more_body"):
                    break
            sent = False

            async def buffered_receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            actual_receive = buffered_receive
        else:
            size = 0

            async def bounded_receive():
                nonlocal size
                message = await receive()
                size += len(message.get("body", b""))
                if size > maximum:
                    raise ApiError(413, "Файл слишком большой.")
                return message

            actual_receive = bounded_receive
        try:
            await self.app(scope, actual_receive, secure_send)
        except ApiError as exception:
            if response_started:
                raise RuntimeError("Ошибка после начала ответа") from None
            await error(exception.status, exception.message)
        except Exception:
            from club_api.observability.errors import capture

            capture("request_failed")
            logger.error("Не удалось обработать запрос")
            if response_started:
                raise RuntimeError("Ошибка после начала ответа") from None
            await error(500, "Внутренняя ошибка.")
        finally:
            logger.info("%s %s %s", method, path, status if response_started else "disconnected")
