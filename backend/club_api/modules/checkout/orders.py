import json
import logging
from typing import Literal
from uuid import UUID, uuid4

from django.http import HttpRequest, JsonResponse
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.domain import effective_discount, order_totals, rubles
from club_api.modules.auth.routes import Body, EmailBody, RegisterBody
from club_api.modules.auth.service import require_alumni
from club_api.modules.catalog.lookup import lookup_catalog
from club_api.modules.checkout.cart import cart_session, load_cart
from club_api.modules.checkout.payments import SAFE_INTEGER_MAX, secure_payment_url
from club_api.modules.checkout.store import digest
from club_api.modules.notifications.mail import club_subject
from club_api.modules.portfolio.captcha import RETRY_MESSAGE
from club_api.modules.portfolio.cohorts import with_starts
from club_api.observability.audit import audit

logger = logging.getLogger("club.orders")
ROUTE_LIMITS = {"/orders": 6}


class OrderBody(Body):
    contact_fio: str = Field(min_length=2, max_length=200)
    contact_phone: str = Field(min_length=5, max_length=40)
    contact_email: str = Field(max_length=200)
    fulfillment: Literal["pickup", "delivery"]
    address: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)
    consent_pdn: Literal[True]
    website: str = Field(default="", max_length=0)

    @field_validator("contact_email")
    @classmethod
    def valid_email(cls, value):
        return EmailBody.email_valid(value)

    @field_validator("consent_pdn", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        return RegisterBody.explicit_consent(value)


def json_string(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@api_view(body=OrderBody)
async def create_order(request: HttpRequest):
    body = parse_body(request, OrderBody)
    state, session = (request.services, cart_session(request))
    if body.fulfillment == "delivery" and (not (body.address or "").strip()):
        raise ApiError(400, "Укажите адрес доставки.")
    alumni = await state.auth.resolve_alumni(request)
    supplied_key = request.headers.get("idempotency-key")
    if supplied_key is not None:
        try:
            if len(supplied_key) != 36:
                raise ValueError
            UUID(supplied_key)
        except ValueError as error:
            raise ApiError(400, RETRY_MESSAGE) from error
    key = digest(session + ":" + (supplied_key or str(uuid4())))
    request_hash = digest(
        json_string({"body": body.model_dump(exclude_unset=True), "alumni": alumni["id"] if alumni else None})
    )
    if previous := (await state.checkout.replay(key, request_hash)):
        return previous
    cart = await load_cart(state.store, session)
    if not cart or not cart["items"]:
        raise ApiError(400, "Корзина пуста.")
    items = cart["items"]
    catalog = await lookup_catalog(state.store, items)
    unavailable = []
    priced = []
    for item in items:
        info = catalog.get(item["type"] + ":" + item["ref_id"])
        if not info:
            unavailable.append(item["title"] or item["ref_id"])
        else:
            priced.append({**item, "price": info["price"], "title": info["title"]})
    if unavailable:
        unavailable = list(dict.fromkeys(unavailable))
        return JsonResponse(
            {
                "error": f"Эти позиции больше недоступны: {', '.join(unavailable)}. Удалите их из корзины и оформите заявку заново.",
                "unavailable": unavailable,
            },
            status=409,
            safe=False,
        )
    discount = effective_discount(
        bool(alumni and alumni["verification_status"] == "verified"),
        alumni["points_cached"] or 0 if alumni else 0,
        alumni["personal_discount"] or 0 if alumni else 0,
    )
    totals = order_totals(priced, discount)
    if not all(type(totals[name]) is int and 0 <= totals[name] <= SAFE_INTEGER_MAX for name in ("subtotal", "total")):
        raise ApiError(400, "Некорректная сумма заявки.")
    types = {item["type"] for item in priced}
    order_type = "mixed" if len(types) > 1 else next(iter(types))
    base = {
        "alumni_id": alumni["id"] if alumni else None,
        "type": order_type,
        "items_json": priced,
        "subtotal": totals["subtotal"],
        "member_discount": discount,
        "total_estimate": totals["total"],
        "contact_fio": body.contact_fio,
        "contact_phone": body.contact_phone,
        "contact_email": body.contact_email,
        "fulfillment": body.fulfillment,
        "address": body.address,
        "comment": body.comment,
        "consent_pdn": True,
        "status": "new",
        "payment_status": "pending" if state.payments.enabled and totals["total"] > 0 else None,
    }
    result = await state.checkout.commit(
        session=session, key=key, request_hash=request_hash, cart_id=cart["id"], cart_items=items, base=base
    )
    if result["replay"]:
        return result["replay"]
    if alumni and order_type in ("merch", "mixed"):
        await state.gamification.refresh_achievements(alumni["id"])
    number = result["number"]
    summary = "; ".join(
        item["title"] + (f" ({item['variant_sku']})" if item.get("variant_sku") else "") + f" ×{item['qty']}"
        for item in priced
    )
    notified = await state.notifications.order_notice(number, summary, totals["total"], discount)
    await state.notifications.mail_applicant(
        body.contact_email,
        club_subject(f"Заявка {number} принята"),
        f"Здравствуйте, {body.contact_fio}!\n\nВаша заявка {number} принята.\n{summary}\n"
        f"Предварительная сумма {rubles(totals['total'])}.\n\nМы свяжемся с Вами, чтобы подтвердить детали.",
    )
    receipt = {
        "number": number,
        "status": "new",
        "member_discount": discount,
        "subtotal": totals["subtotal"],
        "total_estimate": totals["total"],
        "notified": notified,
    }
    if state.payments.enabled and totals["total"] > 0:
        try:
            payment = await state.payments.create(
                number,
                totals["total"],
                f"Заявка {number} · Клуб выпускников факультета права НИУ ВШЭ",
                body.contact_email,
            )
            await state.payments.record(number, payment)
            if url := secure_payment_url(payment.get("confirmation", {}).get("confirmation_url")):
                receipt["payment_url"] = url
        except Exception:
            await state.payments.creation_failed(number)
            logger.error("Не удалось создать платёж по заявке")
    await audit(
        request,
        "order.created",
        actor="alumni:" + alumni["id"] if alumni else "guest",
        subject="order:" + number,
        detail={"total": totals["total"], "discount": discount, "type": order_type},
    )
    try:
        await state.checkout.save_receipt(key, receipt)
    except Exception:
        logger.error("Не удалось обновить квитанцию после сохранения заявки")
    return receipt


@api_view(permission=require_alumni)
async def my_orders(request: HttpRequest):
    alumni = await require_alumni(request)
    orders = await request.services.store.read(
        "orders",
        filters={"alumni_id": {"_eq": alumni["id"]}},
        sort=("-created_at",),
        limit=50,
        fields=(
            "number",
            "type",
            "status",
            "subtotal",
            "member_discount",
            "total_estimate",
            "created_at",
            "items_json",
            "fulfillment",
            "payment_status",
            "participants",
            "organization",
            "office_discount",
            "discount_source",
            "promo_code",
            "cohort_id",
        ),
    )
    return await with_starts(request.services, orders)
