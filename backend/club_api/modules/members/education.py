import logging

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.core.views import api_view
from club_api.modules.auth.service import require_alumni, require_full_admin
from club_api.modules.media.routes import upload_form
from club_api.observability.audit import audit

logger = logging.getLogger("club.education")
ROUTE_LIMITS = {("POST", "/me/education-document"): 6, ("DELETE", "/me/education-document"): 6}
KIND = "education"
PENDING = "pending"
MAX_BYTES = 10 * 1024 * 1024
DOWNLOAD_NAME = "education-document"
ENABLED = "true"
DISABLED = "Загрузка документа об образовании отключена."
ONLY_PENDING = "Документ можно приложить, пока заявка на проверке."
WRONG_TYPE = "Поддерживаются PDF, JPEG, PNG или WebP."
TOO_LARGE = "Файл больше 10 МБ."
CHANGED = "Профиль изменился. Обновите страницу и повторите."
MISSING = "Документ не приложен."
REJECTIONS = {413: TOO_LARGE, 415: WRONG_TYPE}


async def remove_file(state, file_id):
    if not file_id:
        return
    try:
        await state.media.delete(str(file_id))
    except Exception:
        logger.error("Не удалось удалить документ об образовании")


async def attach(state, alumni, file_id):
    async with state.database.transaction() as connection:
        current = await (
            await connection.execute(
                "SELECT education_document,token_version,verification_status FROM alumni WHERE id=%s FOR UPDATE",
                (alumni["id"],),
            )
        ).fetchone()
        if (
            not current
            or current["verification_status"] != PENDING
            or (current["token_version"] or 0) != (alumni["token_version"] or 0)
        ):
            raise ApiError(409, CHANGED)
        await connection.execute("UPDATE alumni SET education_document=%s WHERE id=%s", (file_id, alumni["id"]))
        return current["education_document"]


async def discard(state, alumni_id):
    async with state.database.transaction() as connection:
        current = await (
            await connection.execute("SELECT education_document FROM alumni WHERE id=%s FOR UPDATE", (alumni_id,))
        ).fetchone()
        if not current or not current["education_document"]:
            return False
        await connection.execute("UPDATE alumni SET education_document=NULL WHERE id=%s", (alumni_id,))
    await remove_file(state, current["education_document"])
    return True


def accepts_upload(settings, alumni):
    return settings.EDUCATION_DOCUMENT_ENABLED == ENABLED and alumni["verification_status"] == PENDING


async def pending_alumni(request):
    alumni = await require_alumni(request)
    if request.services.settings.EDUCATION_DOCUMENT_ENABLED != ENABLED:
        raise ApiError(403, DISABLED)
    if alumni["verification_status"] != PENDING:
        raise ApiError(403, ONLY_PENDING)
    return alumni


async def store_upload(request, alumni):
    upload = await upload_form(request)
    try:
        return await request.services.media.save(
            upload, filename=DOWNLOAD_NAME + "-" + alumni["id"], kind=KIND, max_bytes=MAX_BYTES
        )
    except ApiError as error:
        if error.status in REJECTIONS:
            raise ApiError(400, REJECTIONS[error.status]) from None
        raise
    finally:
        upload.close()


@api_view(permission=require_alumni)
async def upload(request: HttpRequest):
    alumni = await pending_alumni(request)
    state = request.services
    file = await store_upload(request, alumni)
    try:
        previous = await attach(state, alumni, file["id"])
    except BaseException:
        await remove_file(state, file["id"])
        raise
    if str(previous or "") != file["id"]:
        await remove_file(state, previous)
    await audit(
        request,
        "education.upload",
        actor="alumni:" + alumni["id"],
        detail={"size": int(file["filesize"]), "type": file["type"]},
    )
    return {"ok": True}


@api_view(permission=require_alumni)
async def remove(request: HttpRequest):
    alumni = await pending_alumni(request)
    previous = await attach(request.services, alumni, None)
    if not previous:
        raise ApiError(404, MISSING)
    await remove_file(request.services, previous)
    await audit(request, "education.remove", actor="alumni:" + alumni["id"])
    return {"ok": True}


@api_view(permission=require_full_admin)
async def office_document(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    state, id = (request.services, guid(id))
    rows = await state.database.rows("SELECT education_document FROM alumni WHERE id=%s", (id,))
    if not rows or not rows[0]["education_document"]:
        raise ApiError(404, MISSING)
    await audit(request, "education.view", actor="admin:" + admin["userId"], subject="alumni:" + id)
    return await state.media.stream(str(rows[0]["education_document"]), kind="document", download=DOWNLOAD_NAME)
