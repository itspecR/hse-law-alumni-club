import logging

from django.http import HttpRequest, JsonResponse

from club_api.core.errors import ApiError
from club_api.core.models import guid, query_page
from club_api.core.views import api_view
from club_api.modules.auth.service import require_admin, require_alumni
from club_api.observability.audit import audit

logger = logging.getLogger("club.media")
ROUTE_LIMITS = {("POST", "/admin/media"): 8, ("POST", "/me/avatar"): 10}


async def upload_form(request):
    from asgiref.sync import sync_to_async

    try:
        uploads = await sync_to_async(lambda: list(request.FILES.values()), thread_sensitive=True)()
        if request.POST or len(uploads) != 1:
            raise ValueError
        return uploads[0]
    except Exception:
        raise ApiError(400, "Прикрепите один файл.") from None


@api_view
async def public_media(request: HttpRequest, fileId: str):
    media, id = (request.services.media, guid(fileId))
    if not await media.is_published_image(id):
        raise ApiError(404, "Файл не найден.")
    return await media.stream(id, kind="image", range=request.headers.get("range"), cache="public, max-age=300")


@api_view(permission=require_admin)
async def media_list(request: HttpRequest):
    await require_admin(request)
    page, limit = query_page(request, default_limit=20)
    search = request.GET.get("q", "")
    if len(search) > 120 or page > 100000:
        raise ApiError(400, "Некорректные параметры.")
    return await request.services.media.list(page, limit, search)


@api_view(permission=require_admin)
async def upload_office(request: HttpRequest):
    admin = await require_admin(request)
    upload = await upload_form(request)
    try:
        file = await request.services.media.save(upload, filename=upload.name or "file", uploaded_by=admin["userId"])
    finally:
        upload.close()
    await audit(
        request,
        "media.upload",
        actor="admin:" + admin["userId"],
        subject="file:" + file["id"],
        detail={"size": int(file["filesize"]), "type": file["type"]},
    )
    return JsonResponse(
        {"id": file["id"], "filename": file["filename_download"], "type": file["type"], "size": int(file["filesize"])},
        status=201,
        safe=False,
    )


async def office_file(media, id):
    file = await media.get(id)
    if not file or (file["metadata"] or {}).get("club_upload_kind") == "avatar" or await media.is_avatar(id):
        raise ApiError(404, "Файл не найден.")
    return file


@api_view(permission=require_admin)
async def office_content(request: HttpRequest, fileId: str):
    await require_admin(request)
    media, id = (request.services.media, guid(fileId))
    file = await office_file(media, id)
    return await media.stream(
        id, kind="image" if file["type"].startswith("image/") else "audio", range=request.headers.get("range")
    )


@api_view(permission=require_admin)
async def delete_media(request: HttpRequest, fileId: str):
    admin = await require_admin(request)
    media, id = (request.services.media, guid(fileId))
    await office_file(media, id)
    await media.delete(id)
    await audit(request, "media.delete", actor="admin:" + admin["userId"], subject="file:" + id)
    return {"ok": True}


@api_view(permission=require_alumni)
async def upload_avatar(request: HttpRequest):
    alumni = await require_alumni(request)
    if alumni["verification_status"] == "rejected":
        raise ApiError(403, "Заявка отклонена, загрузка фото недоступна.")
    if alumni["verification_status"] not in ("pending", "verified"):
        raise ApiError(403, "Доступно после подачи заявки.")
    upload = await upload_form(request)
    state = request.services
    try:
        if upload.content_type not in ("image/jpeg", "image/png", "image/webp"):
            raise ApiError(400, "Поддерживаются JPEG, PNG или WebP.")
        try:
            file = await state.media.save(
                upload, filename="avatar-" + alumni["id"], kind="avatar", max_bytes=3 * 1024 * 1024
            )
        except ApiError as error:
            if error.status in (413, 415):
                await audit(
                    request, "avatar.reject", actor="alumni:" + alumni["id"], detail={"declared": upload.content_type}
                )
                raise ApiError(
                    400, "Файл больше 3 МБ." if error.status == 413 else "Это не изображение JPEG, PNG или WebP."
                ) from None
            raise
    finally:
        upload.close()
    try:
        async with state.database.transaction() as connection:
            current = await (
                await connection.execute(
                    "SELECT avatar,user_id,telegram_id,token_version,verification_status FROM alumni WHERE id=%s FOR UPDATE",
                    (alumni["id"],),
                )
            ).fetchone()
            if (
                not current
                or current["verification_status"] not in ("pending", "verified")
                or str(current["user_id"] or "") != str(alumni["user_id"] or "")
                or current["telegram_id"] != alumni["telegram_id"]
                or (current["token_version"] or 0) != (alumni["token_version"] or 0)
            ):
                raise ApiError(409, "Профиль изменился. Обновите страницу перед загрузкой фото.")
            previous_avatar = current["avatar"]
            await connection.execute("UPDATE alumni SET avatar=%s WHERE id=%s", (file["id"], alumni["id"]))
    except BaseException:
        await state.media.delete(file["id"])
        raise
    if previous_avatar and str(previous_avatar) != file["id"]:
        try:
            await state.media.delete(str(previous_avatar))
        except Exception:
            logger.warning("Не удалось удалить прежний аватар")
    await audit(
        request,
        "avatar.upload",
        actor="alumni:" + alumni["id"],
        detail={"fileId": file["id"], "size": int(file["filesize"])},
    )
    return {"ok": True, "avatar": file["id"]}


@api_view
async def avatar(request: HttpRequest, fileId: str):
    media, id = (request.services.media, guid(fileId))
    if not await media.is_avatar(id):
        raise ApiError(404, "Не найдено.")
    return await media.avatar(id)
