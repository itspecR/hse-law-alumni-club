from asgiref.sync import markcoroutinefunction

from club_web.rendering import published_version
from club_web.views import PUBLIC

VERSIONED_ASSET = "public, max-age=31536000, immutable"
FONT_ASSET = "public, max-age=2592000"
STATIC_ASSET = "public, max-age=86400"
CSP = "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob: https:; media-src 'self' https: blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; frame-src https://rutube.ru https://www.rutube.ru; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors https://web.telegram.org https://*.telegram.org 'self'"


class HeadersMiddleware:
    sync_capable = False
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        markcoroutinefunction(self)

    async def __call__(self, request):
        response = await self.get_response(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cache-Control"] = cache_policy(request, response)
        return response


def cache_policy(request, response):
    static = request.path.startswith(("/assets/", "/fonts/"))
    if response.headers.get("X-Club-Offline") == "public":
        return "no-cache"
    if static and response.status_code == 200 and request.GET.get("v") == published_version(PUBLIC) != "":
        return VERSIONED_ASSET
    if request.path == "/sw.js" or request.path.endswith((".js", ".css")):
        return "no-cache"
    if static and request.path.endswith(".woff2"):
        return FONT_ASSET
    if static:
        return STATIC_ASSET
    return "no-store"
