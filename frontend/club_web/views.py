import asyncio
import mimetypes

import httpx
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse, StreamingHttpResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_safe

from club_web.client import Api, forwarded_headers
from club_web.offline import can_save_page
from club_web.pages import context
from club_web.rendering import ROOT, published_version, templates

PUBLIC = ROOT / "public" if (ROOT / "public").exists() else ROOT.parent / "public"
CONTENT_TYPES = {"speculation-rules.json": "application/speculationrules+json"}
STATIC_FILES = {
    "sw.js",
    "speculation-rules.json",
    "manifest.webmanifest",
    "offline.html",
    "favicon.ico",
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-512.png",
    "icon-180.png",
    "icon-152.png",
    "icon-167.png",
}


@require_safe
async def health(request):
    return JsonResponse({"status": "ok", "service": "club-web"})


async def api_proxy(request, path):
    if request.method not in {"GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"}:
        return JsonResponse({"error": "Метод не поддерживается."}, status=405)
    try:
        size = int(request.headers.get("content-length", "0"))
        if size < 0:
            raise ValueError
    except ValueError:
        return JsonResponse({"error": "Некорректный размер запроса"}, status=400)
    maximum = 129 * 1024 * 1024 if path == "admin/media" else 4 * 1024 * 1024 if path == "me/avatar" else 256 * 1024
    if size > maximum or len(request.body) > maximum:
        return JsonResponse({"error": "Запрос превышает допустимый размер"}, status=413)
    outgoing = {
        key: value
        for key, value in request.headers.items()
        if key.lower()
        in (
            "authorization",
            "accept",
            "content-type",
            "x-cart-session",
            "idempotency-key",
            "x-support-token",
            "x-support-access",
            "x-support-key",
            "range",
        )
    }
    try:
        outgoing_request = request.services.client.build_request(
            request.method, "/" + path.lstrip("/"), params=request.GET.dict(), headers=outgoing, content=request.body
        )
        response = await request.services.client.send(outgoing_request, stream=True)
    except httpx.HTTPError:
        return JsonResponse({"error": "Сервис временно недоступен. Попробуйте позже."}, status=503)
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower()
        in (
            "content-type",
            "content-disposition",
            "cache-control",
            "content-security-policy",
            "etag",
            "last-modified",
            "content-range",
            "accept-ranges",
            "location",
        )
    }

    async def stream():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()

    return StreamingHttpResponse(stream(), status=response.status_code, headers=headers)


async def render_page(request, path, *, fragment):
    supplied = forwarded_headers(request)
    data = await context(
        Api(request.services.client, supplied), "/" + path, request.GET.dict(), "authorization" in supplied
    )
    if fragment and data.get("access_error") == 401:
        return JsonResponse({"error": "Сессия завершилась. Войдите снова."}, status=401)
    data.update(request=request, fragment=fragment, base="/", mirror=False, asset_version=published_version(PUBLIC))
    data["offline_reading"] = can_save_page(
        "/" + path, request.GET, data, fragment=fragment, authorized="authorization" in supplied
    )
    response = HttpResponse(templates().get_template(data["template"]).render(data), status=data["status"])
    if data["offline_reading"]:
        response.headers["X-Club-Offline"] = "public"
    return response


@require_safe
async def fragment(request, path):
    return await render_page(request, path, fragment=True)


@require_safe
async def static_file(request, path, prefix=""):
    path = prefix + "/" + path if prefix else path
    root = PUBLIC.resolve()
    file = (root / path).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        return HttpResponse(status=404)

    body = await asyncio.to_thread(file.read_bytes)
    return HttpResponse(
        body,
        headers={"Content-Length": str(len(body))},
        content_type=CONTENT_TYPES.get(file.name) or mimetypes.guess_type(file.name)[0] or "application/octet-stream",
    )


async def page(request, path=""):
    if request.method not in {"GET", "HEAD"}:
        return HttpResponse(status=405)
    query = request.META.get("QUERY_STRING", "")
    suffix = "?" + query if query else ""
    target = None
    if path.endswith("/") and path:
        target = "/" + path.rstrip("/") + suffix
    elif path.startswith(("v2/", "legacy/")) or path in ("v2", "legacy"):
        target = "/" + path.partition("/")[2] + suffix
    if target is not None:
        if not url_has_allowed_host_and_scheme(target, allowed_hosts=set()):
            return HttpResponse("Некорректный адрес страницы", status=400)
        return HttpResponseRedirect(target, status=308)
    if path in STATIC_FILES:
        return await static_file(request, path)
    return await render_page(request, path, fragment=False)
