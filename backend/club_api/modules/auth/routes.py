import re
from typing import Literal

from django.http import HttpRequest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.names import PersonName
from club_api.core.security import client_ip
from club_api.core.views import api_view, parse_body
from club_api.domain import MAX_INTERESTS
from club_api.modules.auth.codes import ADMIN_PURPOSE, CODE_DIGITS, CONFIRM_PURPOSE, RESET_PURPOSE, ttl_minutes
from club_api.modules.auth.passwords import hash_password
from club_api.modules.auth.service import (
    ALUMNI_ROLE,
    DUPLICATE_ACCOUNT,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    alumni_profile,
    auth_service,
    require_admin,
)
from club_api.modules.notifications.mail import CLUB_NAME, EMAIL_CONFIRMATION_KIND, club_subject
from club_api.modules.telegram.signature import validate_init_data
from club_api.observability.audit import audit

CODE_INVALID = "Неверный или устаревший код. Запросите новый код."
NOT_ALUMNI = "Этот аккаунт не связан с профилем выпускника."
TELEGRAM_LOGIN_FAILED = "Не удалось проверить вход через Telegram."
OFFICE_REVIEW = "Проверьте ее в панели управления, раздел «Выпускники»."
ROUTE_LIMITS = {
    "/auth/login": 5,
    "/auth/admin-login": 5,
    "/auth/admin-code": 10,
    "/auth/register": 3,
    "/auth/telegram": 10,
    "/auth/resend-confirmation": 3,
    "/auth/confirm": 10,
    "/auth/forgot": 3,
    "/auth/reset": 5,
}


class Body(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class EmailBody(Body):
    email: str = Field(max_length=200)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        if not re.fullmatch(
            "(?!\\.)(?!.*\\.\\.)[A-Za-z0-9_'+.\\-]*[A-Za-z0-9_+\\-]@(?:[A-Za-z0-9][A-Za-z0-9\\-]*\\.)+[A-Za-z]{2,}",
            value,
        ):
            raise ValueError("Некорректная почта")
        return value


class LoginBody(EmailBody):
    password: str = Field(min_length=1)


class RegisterBody(EmailBody):
    fio: PersonName
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    cohort: str = Field(pattern="^(19|20)\\d{2}$")
    edu_level: Literal["бакалавриат", "магистратура", "специалитет", "аспирантура"]
    edu_program: str = Field(min_length=2, max_length=200)
    interests: list[str] = Field(default_factory=list)
    ref: str | None = Field(default=None, max_length=40)
    consent_pdn: Literal[True]
    consent_mailing: bool = False
    website: str = Field(default="", max_length=0)

    @field_validator("consent_pdn", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Требуется согласие на обработку ПДн")
        return value


class CodeBody(EmailBody):
    code: str = Field(min_length=CODE_DIGITS, max_length=CODE_DIGITS * 2)


class ResetBody(CodeBody):
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class TelegramBody(Body):
    initData: str = Field(min_length=1, max_length=16384)


def confirmation_letter(code, public_url):
    return (
        f"Здравствуйте!\n\nВы подали заявку на вступление в {CLUB_NAME}. Код подтверждения почты: {code}\n\n"
        f"Код действует {ttl_minutes(CONFIRM_PURPOSE)} минут. Введите его на странице {public_url}/confirm\n\n"
        "После подтверждения мы проверим заявку.\n\n"
        "Если заявку подавали не Вы, проигнорируйте письмо, аккаунт останется неактивным."
    )


def reset_letter(code, public_url):
    return (
        f"Здравствуйте!\n\nВы запросили восстановление пароля. Код: {code}\n\n"
        f"Код действует {ttl_minutes(RESET_PURPOSE)} минут и срабатывает один раз. "
        f"Введите его на странице {public_url}/reset\n\n"
        "Если это были не Вы, проигнорируйте письмо."
    )


def staff_letter(code):
    return (
        f"Здравствуйте!\n\nКод для входа в панель управления: {code}\n\n"
        f"Код действует {ttl_minutes(ADMIN_PURPOSE)} минут и срабатывает один раз.\n\n"
        "Если Вы не входили в панель, сообщите администратору клуба: кто-то знает Ваш пароль."
    )


def staff_session(service, user):
    return {"token": service.staff_session(user["id"], user["role"], user["staff_version"]), "role": user["role"]}


async def staff_second_step(request, user):
    service = auth_service(request)
    if not request.services.notifications.mail_enabled:
        await audit(request, "admin.login.ok", actor="user:" + user["id"])
        return staff_session(service, user)
    code = await service.send_code(user["id"], ADMIN_PURPOSE)
    sent = bool(code) and await request.services.notifications.send_email(
        user["email"], club_subject("Код входа в панель управления"), staff_letter(code)
    )
    await audit(request, "admin.login.code", actor="user:" + user["id"], detail={"sent": bool(sent)})
    return {"code_required": True, "sent": bool(sent)}


async def queue_confirmation(request, user_id, email):
    service = auth_service(request)
    code = await service.send_code(user_id, CONFIRM_PURPOSE)
    if not code:
        return {"sent": False, "id": None}
    return await request.services.notifications.enqueue_mail(
        email,
        club_subject("Код подтверждения почты"),
        confirmation_letter(code, service.settings.PUBLIC_URL),
        kind=EMAIL_CONFIRMATION_KIND,
        owner_user_id=user_id,
    )


def registration_reply(confirm_required, queued=False):
    reply = {"ok": True, "pending": True, "confirm_required": confirm_required}
    return {**reply, "confirmation_queued": queued} if confirm_required else reply


async def concealed_registration(request, email, password):
    await hash_password(password)
    await audit(request, "register.concealed", actor="email:" + email)
    return registration_reply(request.services.notifications.mail_enabled, True)


def alumni_view(alumni):
    return {key: alumni[key] for key in ("fio", "cohort", "verification_status")}


async def login(request, body, scope):
    service = auth_service(request)
    email, ip = (body.email.lower().strip(), client_ip(request))
    prefix = "admin.login" if scope == "admin" else "login"
    if service.attempts.locked(email, ip):
        await audit(request, prefix + ".locked", actor="email:" + email)
        raise ApiError(429, "Слишком много неудачных попыток. Попробуйте позже.")
    status, user = await service.authenticate(email, body.password, scope=scope)
    if status != "ok":
        service.attempts.fail(email, ip)
        await audit(request, prefix + ".fail", actor="email:" + email)
        if status == "unverified":
            raise ApiError(
                403, "Почта не подтверждена. Введите код из письма на странице подтверждения, проверьте и папку «Спам»."
            )
        if status == "forbidden":
            raise ApiError(403, "Доступ в панель управления закрыт." if scope == "admin" else NOT_ALUMNI)
        raise ApiError(401, "Неверная почта или пароль.")
    if scope == "admin":
        service.attempts.success(email, ip)
        return await staff_second_step(request, user)
    alumni = await service.profile(user["id"])
    if not alumni or alumni["id"] != user["alumni_id"]:
        raise ApiError(403, NOT_ALUMNI)
    service.attempts.success(email, ip)
    await audit(request, "login.ok", actor="alumni:" + alumni["id"])
    return {
        "token": service.session(user["alumni_id"], user["id"], user["alumni_version"]),
        "alumni": alumni_view(alumni),
    }


@api_view(body=LoginBody)
async def alumni_login(request: HttpRequest):
    body = parse_body(request, LoginBody)
    return await login(request, body, "alumni")


@api_view(body=LoginBody)
async def admin_login(request: HttpRequest):
    body = parse_body(request, LoginBody)
    return await login(request, body, "admin")


@api_view(body=CodeBody)
async def admin_code(request: HttpRequest):
    body = parse_body(request, CodeBody)
    service = auth_service(request)
    email = body.email.lower().strip()
    user = await service.user(email=email, staff=True, active=True)
    if not user or not await service.staff_code(user["id"], body.code):
        await audit(request, "admin.login.code_fail", actor="email:" + email)
        raise ApiError(400, CODE_INVALID)
    await audit(request, "admin.login.ok", actor="user:" + user["id"])
    return staff_session(service, user)


@api_view(permission=require_admin)
async def admin_logout(request: HttpRequest):
    admin = await require_admin(request)
    if admin.get("jti"):
        await auth_service(request).revoke_admin(admin["jti"])
    await audit(request, "admin.logout", actor="user:" + admin["userId"])
    return {"ok": True}


@api_view(permission=require_admin)
async def admin_session(request: HttpRequest):
    admin = await require_admin(request)
    return {"role": admin["role"]}


@api_view(body=RegisterBody)
async def register(request: HttpRequest):
    body = parse_body(request, RegisterBody)
    service = auth_service(request)
    email = body.email.lower().strip()
    existing = await service.user(email=email)
    if existing and existing["role"] == ALUMNI_ROLE:
        raise ApiError(409, DUPLICATE_ACCOUNT)
    if existing:
        return await concealed_registration(request, email, body.password)
    referred_by = None
    if body.ref:
        rows = await service.store.read("alumni", filters={"referral_code": {"_eq": body.ref}}, fields=["id"], limit=1)
        referred_by = rows[0]["id"] if rows else None
    interests = request.services.domain["legal_interests"]
    confirm_required = request.services.notifications.mail_enabled
    user_id = await service.register(
        email=email,
        password=body.password,
        status="unverified" if confirm_required else "active",
        profile=alumni_profile(
            fio=body.fio,
            cohort=body.cohort,
            edu_level=body.edu_level,
            edu_program=body.edu_program,
            interests=list(dict.fromkeys(value for value in body.interests if value in interests))[:MAX_INTERESTS],
            referred_by=referred_by,
            mailing=body.consent_mailing,
        ),
    )
    await audit(
        request,
        "register",
        actor="email:" + email,
        detail={"cohort": body.cohort, "edu_program": body.edu_program, "confirm_required": confirm_required},
    )
    if confirm_required:
        result = await queue_confirmation(request, user_id, email)
        return registration_reply(True, result["sent"] or result["id"] is not None)
    await request.services.notifications.office_text(f"Новая заявка на вступление в клуб. {OFFICE_REVIEW}")
    return registration_reply(False)


@api_view(body=EmailBody)
async def resend_confirmation(request: HttpRequest):
    body = parse_body(request, EmailBody)
    if not request.services.notifications.mail_enabled:
        raise ApiError(503, "Отправка писем временно недоступна. Повторите позже.")
    service = auth_service(request)
    email = body.email.lower().strip()
    user = await service.user(email=email, alumni=True)
    if user and user["status"] == "unverified" and await service.profile(user["id"]):
        await queue_confirmation(request, user["id"], email)
    return {"ok": True}


@api_view(body=CodeBody)
async def confirm(request: HttpRequest):
    body = parse_body(request, CodeBody)
    service = auth_service(request)
    user = await service.user(email=body.email.lower().strip(), alumni=True)
    result = await service.confirm(user["id"], body.code) if user else "invalid"
    if result == "invalid":
        raise ApiError(400, CODE_INVALID)
    if result == "already":
        return {"ok": True, "already": True}
    await audit(request, "email.confirm", actor="user:" + user["id"])
    await request.services.notifications.office_text(
        f"Новая заявка на вступление в клуб, почта подтверждена. {OFFICE_REVIEW}"
    )
    return {"ok": True}


@api_view(body=EmailBody)
async def forgot(request: HttpRequest):
    body = parse_body(request, EmailBody)
    service = auth_service(request)
    if not request.services.notifications.mail_enabled:
        raise ApiError(503, "Восстановление пароля временно недоступно. Напишите в поддержку.")
    user = await service.user(email=body.email.lower().strip(), alumni=True)
    if user and await service.profile(user["id"]):
        code = await service.send_code(user["id"], RESET_PURPOSE)
        if code:
            await audit(request, "password.forgot", actor="email:" + body.email)
            await request.services.notifications.send_email(
                body.email,
                club_subject("Код для восстановления пароля"),
                reset_letter(code, service.settings.PUBLIC_URL),
            )
    return {"ok": True}


@api_view(body=ResetBody)
async def reset(request: HttpRequest):
    body = parse_body(request, ResetBody)
    service = auth_service(request)
    user = await service.user(email=body.email.lower().strip(), alumni=True)
    if not user or await service.reset(user["id"], body.password, body.code) == "invalid":
        raise ApiError(400, CODE_INVALID)
    await audit(request, "password.reset", actor="user:" + user["id"])
    return {"ok": True}


@api_view(body=TelegramBody)
async def telegram_login(request: HttpRequest):
    body = parse_body(request, TelegramBody)
    service = auth_service(request)
    token = service.settings.secret("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ApiError(503, "Вход через Telegram временно недоступен.")
    user = validate_init_data(body.initData, token)
    if not user:
        raise ApiError(401, TELEGRAM_LOGIN_FAILED)
    if type(user.get("id")) is not int or not 0 < user["id"] <= 9007199254740991:
        raise ApiError(401, TELEGRAM_LOGIN_FAILED)
    telegram_id = str(user["id"])
    rows = await service.store.read(
        "alumni", filters={"telegram_id": {"_eq": telegram_id}}, fields=ALUMNI_FIELDS_TELEGRAM, limit=1
    )
    if not rows:
        raise ApiError(404, "Telegram не подключен к профилю выпускника.")
    alumni = rows[0]
    if alumni["user_id"] and (not await service.user(id=alumni["user_id"], alumni=True, active=True)):
        raise ApiError(403, "Вход в профиль закрыт. Обратитесь в поддержку.")
    return {
        "token": service.session(alumni["id"], alumni["user_id"] or telegram_id, alumni["token_version"] or 0),
        "alumni": alumni_view(alumni),
    }


ALUMNI_FIELDS_TELEGRAM = ("id", "fio", "cohort", "verification_status", "user_id", "token_version")
