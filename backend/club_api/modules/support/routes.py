import json
import re
from datetime import UTC, datetime
from typing import Literal

from django.http import HttpRequest
from psycopg.types.json import Jsonb
from pydantic import ConfigDict, Field, field_validator, model_validator

from club_api.core.errors import ApiError
from club_api.core.models import guid, query_page
from club_api.core.views import api_view, parse_body
from club_api.db.queries import Query
from club_api.db.store import normalize
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin, require_full_admin
from club_api.modules.checkout.store import digest
from club_api.modules.support.service import FAQ, faq_stats, log_faq, support_config
from club_api.observability.audit import audit

ROUTE_LIMITS = {
    ("POST", "/support/faq-event"): 30,
    ("POST", "/support/ask"): 30,
    ("POST", "/support"): (3, 600),
    ("GET", "/support/{id}"): 30,
    ("POST", "/support/{id}/messages"): (10, 600),
    ("DELETE", "/support/{id}"): 10,
}


class StrictBody(Body):
    model_config = ConfigDict(strict=True, extra="forbid")


class MessageBody(StrictBody):
    message: str = Field(min_length=5, max_length=4000)

    @field_validator("message", mode="before")
    @classmethod
    def trimmed(cls, value):
        return value.strip() if isinstance(value, str) else value


class TicketBody(MessageBody):
    id: str
    key: str = Field(pattern="^[a-f0-9]{64}$")
    topic: Literal["account", "order", "personal_data", "other"]
    consent: Literal[True]
    consentVersion: str
    website: str = Field(default="", max_length=0)

    @field_validator("id")
    @classmethod
    def identifier(cls, value):
        return guid(value)

    @field_validator("consent", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("Требуется согласие")
        return value


class ReplyBody(MessageBody):
    message: str = Field(default=None, min_length=5, max_length=4000)
    status: Literal["answered", "closed"]

    @model_validator(mode="after")
    def reply_required(self):
        if self.status == "answered" and (not self.message):
            raise ValueError("Введите ответ")
        return self


class FaqBody(Body):
    kind: Literal["gap", "none"]
    gapId: str = Field(default=None, min_length=1, max_length=64)
    channel: Literal["site", "telegram"] = "site"


class QuestionBody(StrictBody):
    question: str = Field(min_length=2, max_length=500)


def support_key(request):
    value = request.headers.get("x-support-key", "")
    if not re.fullmatch("[a-f0-9]{64}", value):
        raise ApiError(400, "Проверьте номер обращения и код доступа.")
    return digest(value)


def addition(text, author):
    return Jsonb([{"author": author, "text": text, "at": normalize(datetime.now(UTC))}])


@api_view
async def config(request: HttpRequest):
    return support_config(request.services.settings)


@api_view(body=FaqBody)
async def faq_event(request: HttpRequest):
    body = parse_body(request, FaqBody)
    await log_faq(request.services, kind=body.kind, gap_id=body.gapId, channel=body.channel)
    return {"ok": True}


@api_view(body=QuestionBody)
async def ask(request: HttpRequest):
    body = parse_body(request, QuestionBody)
    from club_api.modules.telegram.faq import site_reply

    return await site_reply(request.services, body.question)


@api_view(body=TicketBody)
async def create_ticket(request: HttpRequest):
    body = parse_body(request, TicketBody)
    state = request.services
    cfg = support_config(state.settings)
    if not cfg["enabled"]:
        raise ApiError(503, "Поддержка пока не принимает обращения.")
    if body.consentVersion != cfg["version"]:
        raise ApiError(409, "Условия изменились. Обновите страницу и ознакомьтесь с согласием.")
    request_hash = digest(
        json.dumps([body.topic, body.message, body.consentVersion], ensure_ascii=False, separators=(",", ":"))
    )
    if state.database.vendor == "mysql":
        from club_api.modules.support.storage import create_ticket as store_ticket

        rows = await store_ticket(
            state.database, body, cfg, digest(body.key), request_hash, addition(body.message, "visitor").obj
        )
    else:
        rows = await state.database.rows(
            Query(
                "INSERT INTO club_support_tickets(id,key_hash,request_hash,topic,messages,consent_version,consent_text,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now()+%s*interval '1 day') ON CONFLICT(id) DO NOTHING RETURNING id",
                "INSERT INTO club_support_tickets(id,key_hash,request_hash,topic,messages,consent_version,consent_text,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,now()+INTERVAL %s DAY) ON CONFLICT(id) DO NOTHING RETURNING id",
            ),
            (
                body.id,
                digest(body.key),
                request_hash,
                body.topic,
                addition(body.message, "visitor"),
                cfg["version"],
                cfg["consent"],
                cfg["retentionDays"],
            ),
        )
    if not rows:
        prior = await state.database.rows(
            "SELECT id FROM club_support_tickets WHERE id=%s AND key_hash=%s AND request_hash=%s AND expires_at>now()",
            (body.id, digest(body.key), request_hash),
        )
        if not prior:
            raise ApiError(409, "Обновите страницу и отправьте обращение еще раз.")
    return {"id": body.id, "status": "open"}


@api_view
async def ticket(request: HttpRequest, id: str):
    rows = await request.services.database.rows(
        "SELECT id,topic,messages,status,created_at,expires_at FROM club_support_tickets WHERE id=%s AND key_hash=%s AND expires_at>now()",
        (guid(id), support_key(request)),
    )
    if not rows:
        raise ApiError(404, "Обращение не найдено. Проверьте номер и код доступа.")
    return normalize(rows[0])


@api_view(body=MessageBody)
async def message(request: HttpRequest, id: str):
    body = parse_body(request, MessageBody)
    state = request.services
    if state.database.vendor == "mysql":
        from club_api.modules.support.storage import change_ticket

        rows = await change_ticket(
            state.database,
            guid(id),
            key_hash=support_key(request),
            messages=addition(body.message, "visitor").obj,
            days=state.settings.SUPPORT_RETENTION_DAYS,
        )
    else:
        rows = await state.database.rows(
            Query(
                "UPDATE club_support_tickets SET messages=messages||%s::jsonb,status='open',updated_at=now(),expires_at=now()+%s*interval '1 day' WHERE id=%s AND key_hash=%s AND expires_at>now() AND status<>'closed' AND jsonb_array_length(messages)<50 RETURNING id",
                "UPDATE club_support_tickets SET messages=messages||%s::jsonb,status='open',updated_at=now(),expires_at=now()+INTERVAL %s DAY WHERE id=%s AND key_hash=%s AND expires_at>now() AND status<>'closed' AND JSON_LENGTH(messages)<50 RETURNING id",
            ),
            (addition(body.message, "visitor"), state.settings.SUPPORT_RETENTION_DAYS, guid(id), support_key(request)),
        )
    if not rows:
        raise ApiError(409, "Обращение закрыто или в нем уже максимум сообщений.")
    return {"ok": True}


@api_view
async def delete_ticket(request: HttpRequest, id: str):
    if request.services.database.vendor == "mysql":
        from club_api.modules.support.storage import change_ticket

        rows = await change_ticket(request.services.database, guid(id), key_hash=support_key(request), delete=True)
    else:
        rows = await request.services.database.rows(
            "DELETE FROM club_support_tickets WHERE id=%s AND key_hash=%s RETURNING id",
            (guid(id), support_key(request)),
        )
    if not rows:
        raise ApiError(404, "Обращение не найдено.")
    return {"ok": True}


@api_view(permission=require_full_admin)
async def admin_tickets(request: HttpRequest):
    admin = await require_full_admin(request)
    page, _ = query_page(request, default_limit=30)
    rows = await request.services.database.rows(
        "SELECT id,topic,messages,status,created_at,expires_at FROM club_support_tickets WHERE expires_at>now() ORDER BY updated_at DESC LIMIT 30 OFFSET %s",
        ((page - 1) * 30,),
    )
    await audit(request, "support.read", actor="admin:" + admin["userId"])
    return normalize(rows)


@api_view(body=ReplyBody, permission=require_full_admin)
async def answer_ticket(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    body = parse_body(request, ReplyBody)
    state = request.services
    if state.database.vendor == "mysql":
        from club_api.modules.support.storage import change_ticket

        rows = await change_ticket(
            state.database,
            guid(id),
            messages=addition(body.message, "support").obj if body.message else [],
            status=body.status,
            days=state.settings.SUPPORT_RETENTION_DAYS,
        )
    else:
        rows = await state.database.rows(
            "UPDATE club_support_tickets SET messages=messages||%s::jsonb,status=%s,updated_at=now(),expires_at=now()+%s*interval '1 day' WHERE id=%s AND expires_at>now() AND (%s OR jsonb_array_length(messages)<50) RETURNING id",
            (
                addition(body.message, "support") if body.message else Jsonb([]),
                body.status,
                state.settings.SUPPORT_RETENTION_DAYS,
                guid(id),
                not bool(body.message),
            ),
        )
    if not rows:
        raise ApiError(404, "Обращение недоступно.")
    await audit(request, "support.update", actor="admin:" + admin["userId"], subject=id, detail={"status": body.status})
    return {"ok": True}


@api_view(permission=require_admin)
async def bot_status(request: HttpRequest):
    await require_admin(request)
    state = request.services
    username = state.settings.TELEGRAM_BOT_USERNAME or "pravohse_alumni_bot"
    open_count = None
    if state.database.pool:
        rows = await state.database.rows(
            Query(
                "SELECT count(*)::int AS count FROM club_support_tickets WHERE expires_at>now() AND status='open'",
                "SELECT count(*) AS count FROM club_support_tickets WHERE expires_at>now() AND status='open'",
            )
        )
        open_count = rows[0]["count"]
    cfg = support_config(state.settings)
    return {
        "telegram": {
            "username": username,
            "tokenConfigured": bool(state.settings.secret("TELEGRAM_BOT_TOKEN")),
            "polling": state.settings.TELEGRAM_POLLING == "true",
            "link": "https://t.me/" + username,
        },
        "siteFaq": {
            "answers": len(FAQ["answers"]),
            "gaps": len(FAQ["gaps"]),
            "note": "Страница поддержки и бот @pravohse_alumni_bot используют один FAQ. Программы ДПО бот берет из каталога сайта.",
            "hits": await faq_stats(state),
        },
        "tickets": {"enabled": cfg["enabled"], "draft": cfg["draft"], "openApprox": open_count},
    }
