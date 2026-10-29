import re
import secrets
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote

from django.http import HttpRequest
from pydantic import BaseModel, ConfigDict, Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.security import client_ip
from club_api.core.views import api_view, parse_body
from club_api.db.queries import Query
from club_api.domain import MAX_INTERESTS
from club_api.modules.auth.service import DUPLICATE_ACCOUNT, auth_service, require_admin
from club_api.modules.notifications.mail import CLUB_NAME, EMAIL_CONFIRMATION_KIND, club_subject
from club_api.modules.telegram.signature import validate_init_data
from club_api.observability.audit import audit
from club_api.resources import shared_data

PDN_POLICY_VERSION = shared_data("legal-data.json")["version"]
RESEND_COOLDOWN_SECONDS = 600
RESEND_CACHE_LIMIT = 10000
NOT_ALUMNI = "Этот аккаунт не связан с профилем выпускника."
TELEGRAM_LOGIN_FAILED = "Не удалось проверить вход через Telegram."
OFFICE_REVIEW = "Проверьте ее в панели управления, раздел «Выпускники»."
ROUTE_LIMITS = {
    "/auth/login": 5,
    "/auth/admin-login": 5,
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
    fio: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=100)
    cohort: str = Field(pattern="^(19|20)\\d{2}$")
    edu_level: Literal["бакалавриат", "магистратура", "специалитет", "аспирантура"]
    edu_program: str = Field(min_length=2, max_length=200)
    interests: list[str] = Field(default_factory=list)
    ref: str | None = Field(default=None, max_length=40)
    consent_pdn: Literal[True]
    website: str = Field(default="", max_length=0)

    @field_validator("consent_pdn", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Требуется согласие на обработку ПДн")
        return value


class TokenBody(Body):
    token: str = Field(min_length=10)


class ResetBody(TokenBody):
    password: str = Field(min_length=8, max_length=100)


class ForgotBody(EmailBody):
    next: str | None = Field(default=None, max_length=200)


class TelegramBody(Body):
    initData: str = Field(min_length=1, max_length=16384)


async def queue_confirmation(request, user_id, email):
    service = auth_service(request)
    token = service.token({"sub": user_id, "purpose": "email-confirm"}, 86400)
    body = f"Здравствуйте!\n\nВы подали заявку на вступление в {CLUB_NAME}. Чтобы подтвердить почту, откройте ссылку, она действует 24 часа:\n{service.settings.PUBLIC_URL}/confirm?token={quote(token)}\n\nПосле подтверждения мы проверим заявку.\n\nЕсли заявку подавали не Вы, проигнорируйте письмо, аккаунт останется неактивным."
    return await request.services.notifications.enqueue_mail(
        email,
        club_subject("Подтвердите почту"),
        body,
        kind=EMAIL_CONFIRMATION_KIND,
        owner_user_id=user_id,
    )


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
            raise ApiError(403, "Почта не подтверждена. Откройте ссылку из письма, проверьте и папку «Спам».")
        if status == "forbidden":
            raise ApiError(403, "Доступ в панель управления закрыт." if scope == "admin" else NOT_ALUMNI)
        raise ApiError(401, "Неверная почта или пароль.")
    if scope == "admin":
        service.attempts.success(email, ip)
        await audit(request, "admin.login.ok", actor="user:" + user["id"])
        return {"token": service.staff_session(user["id"], user["role"], user["staff_version"]), "role": user["role"]}
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
    if await service.user(email=email):
        raise ApiError(409, DUPLICATE_ACCOUNT)
    referred_by = None
    if body.ref:
        rows = await service.store.read("alumni", filters={"referral_code": {"_eq": body.ref}}, fields=["id"], limit=1)
        referred_by = rows[0]["id"] if rows else None
    interests = request.services.domain["legal_interests"]
    confirm_required = request.services.notifications.mail_enabled
    user_id = await service.register(
        email=email,
        password=body.password,
        first_name=body.fio.split(" ")[0],
        last_name=" ".join(body.fio.split(" ")[1:]) or "-",
        status="unverified" if confirm_required else "active",
        profile={
            "fio": body.fio.strip(),
            "cohort": body.cohort,
            "edu_level": body.edu_level,
            "edu_program": body.edu_program.strip(),
            "interests_json": list(dict.fromkeys(value for value in body.interests if value in interests))[
                :MAX_INTERESTS
            ],
            "referral_code": "RC-" + secrets.token_hex(4),
            "referred_by": referred_by,
            "consent_at": datetime.now(UTC),
            "consent_version": PDN_POLICY_VERSION,
        },
    )
    await audit(
        request,
        "register",
        actor="email:" + email,
        detail={"cohort": body.cohort, "edu_program": body.edu_program, "confirm_required": confirm_required},
    )
    if confirm_required:
        result = await queue_confirmation(request, user_id, email)
        return {
            "ok": True,
            "pending": True,
            "confirm_required": True,
            "confirmation_queued": result["sent"] or result["id"] is not None,
        }
    await request.services.notifications.office_text(f"Новая заявка на вступление в клуб. {OFFICE_REVIEW}")
    return {"ok": True, "pending": True, "confirm_required": False}


@api_view(body=EmailBody)
async def resend_confirmation(request: HttpRequest):
    body = parse_body(request, EmailBody)
    if not request.services.notifications.mail_enabled:
        raise ApiError(503, "Отправка писем временно недоступна. Повторите позже.")
    service = auth_service(request)
    email, now = (body.email.lower().strip(), time.monotonic())
    entries = service.confirmation_resends
    if entries.get(email, 0) > now:
        return {"ok": True}
    if len(entries) >= RESEND_CACHE_LIMIT:
        for address, until in list(entries.items()):
            if until <= now:
                del entries[address]
        if len(entries) >= RESEND_CACHE_LIMIT:
            raise ApiError(429, "Слишком много запросов. Попробуйте позже.")
    entries[email] = now + RESEND_COOLDOWN_SECONDS
    try:
        recent = await service.database.rows(
            Query(
                "SELECT id FROM club_mail_outbox WHERE kind=%s AND to_addr=%s AND created_at > now() - interval '10 minutes' LIMIT 1",
                "SELECT id FROM club_mail_outbox WHERE kind=%s AND to_addr=%s AND created_at > now() - INTERVAL 10 MINUTE LIMIT 1",
            ),
            (EMAIL_CONFIRMATION_KIND, email),
        )
        if recent:
            return {"ok": True}
        user = await service.user(email=email, alumni=True)
        if user and user["status"] == "unverified" and await service.profile(user["id"]):
            await queue_confirmation(request, user["id"], email)
    except Exception:
        entries.pop(email, None)
        raise
    return {"ok": True}


@api_view(body=TokenBody)
async def confirm(request: HttpRequest):
    body = parse_body(request, TokenBody)
    service = auth_service(request)
    payload = service.decode(body.token)
    if not payload:
        raise ApiError(400, "Ссылка недействительна или истекла. Подайте заявку заново.")
    if payload.get("purpose") != "email-confirm":
        raise ApiError(400, "Ссылка недействительна.")
    result = await service.confirm(payload["sub"])
    if result == "invalid":
        raise ApiError(400, "Ссылка недействительна.")
    if result == "already":
        return {"ok": True, "already": True}
    await audit(request, "email.confirm", actor="user:" + payload["sub"])
    await request.services.notifications.office_text(
        f"Новая заявка на вступление в клуб, почта подтверждена. {OFFICE_REVIEW}"
    )
    return {"ok": True}


@api_view(body=ForgotBody)
async def forgot(request: HttpRequest):
    body = parse_body(request, ForgotBody)
    service = auth_service(request)
    if not request.services.notifications.mail_enabled:
        raise ApiError(503, "Восстановление пароля временно недоступно. Напишите в поддержку.")
    user = await service.user(email=body.email.lower().strip(), alumni=True)
    if user:
        profile = await service.profile(user["id"])
        if not profile:
            return {"ok": True}
        token = service.token(
            {"sub": user["id"], "purpose": "reset", "jti": secrets.token_hex(16), "ver": profile["token_version"]}, 1800
        )
        continuation = "&next=" + quote(body.next, safe="") if body.next == "/podcasts#podcast-subscription" else ""
        url = f"{service.settings.PUBLIC_URL}/reset?token={quote(token)}{continuation}"
        await audit(request, "password.forgot", actor="email:" + body.email)
        await request.services.notifications.send_email(
            body.email,
            club_subject("Восстановление пароля"),
            f"Здравствуйте!\n\nВы запросили восстановление пароля. Ссылка действует 30 минут и срабатывает один раз:\n{url}\n\nЕсли это были не Вы, проигнорируйте письмо.",
        )
    return {"ok": True}


@api_view(body=ResetBody)
async def reset(request: HttpRequest):
    body = parse_body(request, ResetBody)
    service = auth_service(request)
    payload = service.decode(body.token)
    if not payload:
        raise ApiError(400, "Ссылка недействительна или истекла. Запросите новую.")
    if (
        payload.get("purpose") != "reset"
        or not isinstance(payload.get("jti"), str)
        or (not await service.user(id=payload["sub"], alumni=True))
    ):
        raise ApiError(400, "Ссылка недействительна.")
    result = await service.reset(payload["sub"], body.password, payload["jti"], payload.get("ver"))
    if result == "invalid":
        raise ApiError(400, "Ссылка недействительна.")
    if result == "used":
        await audit(request, "password.reset.replay", actor="user:" + payload["sub"])
        raise ApiError(400, "Ссылка уже использована. Запросите новую.")
    await audit(request, "password.reset", actor="user:" + payload["sub"])
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
