import csv
import io
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse

from club_api.core.models import count, guid, parse_date, query_choice, query_page, query_search
from club_api.core.views import api_view, defer, parse_body
from club_api.domain import PORTFOLIO_CATALOG, applied_discount
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin, require_full_admin
from club_api.observability.audit import audit

STATUS_LABELS = {
    "new": "Новая",
    "in_progress": "В работе",
    "confirmed": "Подтверждена",
    "done": "Выполнена",
    "canceled": "Отменена",
    "expired": "Истёк резерв",
}
STATUS_VERBS = {
    "new": "принята",
    "in_progress": "взята в работу",
    "confirmed": "подтверждена",
    "done": "выполнена",
    "canceled": "отменена",
    "expired": "закрыта: истёк резерв",
}
ORDER_FIELDS = (
    "id",
    "number",
    "type",
    "contact_fio",
    "contact_phone",
    "contact_email",
    "fulfillment",
    "status",
    "payment_status",
    "subtotal",
    "total_estimate",
    "created_at",
    "items_json",
    "address",
    "comment",
    "organization",
    "participants",
    "promo_code",
    "discount_source",
    "member_discount",
    "office_discount",
    "office_reason",
)


class StatusBody(Body):
    status: Literal["new", "in_progress", "confirmed", "done", "canceled", "expired"]


def csv_text(rows):
    output = io.StringIO(newline="")
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for row in rows:
        values = []
        for value in row:
            text = "" if value is None else str(value)
            if text.startswith(("=", "+", "-", "@", "\t", "\r")):
                text = "'" + text
            values.append(text)
        writer.writerow(values)
    return output.getvalue()


def csv_response(rows, name):
    return HttpResponse(
        csv_text(rows),
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}-{datetime.now(UTC):%Y-%m-%d}.csv"'},
    )


@api_view(permission=require_admin)
async def orders(request: HttpRequest):
    await require_admin(request)
    store = request.services.store
    page, limit = query_page(request)
    status = query_choice(request, "status", STATUS_LABELS)
    payment = query_choice(
        request, "payment", ("succeeded", "pending", "canceled", "none", "review", "waiting_for_capture")
    )
    search, filters = (query_search(request), [])
    if status:
        filters.append({"status": {"_eq": status}})
    if payment:
        filters.append(
            {"payment_status": {"_null": True}} if payment == "none" else {"payment_status": {"_eq": payment}}
        )
    if search:
        filters.append(
            {
                "_or": [
                    {field: {"_icontains": search}}
                    for field in ("number", "contact_fio", "contact_email", "contact_phone")
                ]
            }
        )
    where = {"_and": filters} if filters else None
    return {
        "items": await store.read(
            "orders", filters=where, fields=ORDER_FIELDS, sort=("-created_at",), limit=limit, offset=(page - 1) * limit
        ),
        "total": await count(store, "orders", where),
        "page": page,
        "limit": limit,
    }


async def notify_status(state, id, status):
    order = await state.store.one("orders", id, fields=("number", "contact_email", "contact_fio", "alumni_id"))
    verb = STATUS_VERBS[status]
    if order["contact_email"] and order["contact_email"] != "-":
        await state.notifications.mail_applicant(
            order["contact_email"],
            f"Заявка {order['number']}: {verb}",
            f"Здравствуйте, {order['contact_fio']}!\n\nСтатус вашей заявки {order['number']} изменился: {verb}.\nДетали – в личном кабинете клуба.\n\n– Клуб выпускников факультета права Вышки",
        )
    if order["alumni_id"]:
        await state.push.to_alumni(
            order["alumni_id"], {"title": "Статус заявки", "body": f"Заявка {order['number']} {verb}", "url": "/lk"}
        )


@api_view(body=StatusBody, permission=require_admin)
async def patch_order(request: HttpRequest, id: str):
    admin = await require_admin(request)
    body = parse_body(request, StatusBody)
    changed = await request.services.checkout.change_status(guid(id), body.status)
    if changed:
        await audit(
            request,
            "order.status",
            actor="admin:" + admin["userId"],
            subject="order:" + id,
            detail={"status": body.status},
        )
        defer(request, notify_status, request.services, id, body.status)
    return {"ok": True, "status": body.status}


def fulfillment_label(order):
    if order["type"] == PORTFOLIO_CATALOG:
        return ""
    return "Доставка" if order["fulfillment"] == "delivery" else "Самовывоз"


@api_view(permission=require_full_admin)
async def export_orders(request: HttpRequest):
    admin = await require_full_admin(request)
    orders = await request.services.store.read("orders", sort=("-created_at",), limit=-1)
    types = {
        "dpo": "ДПО",
        "merch": "Мерч",
        "mixed": "Смешанная",
        "podcast": "Подписка на подкасты",
        PORTFOLIO_CATALOG: "Портфель программ",
    }
    rows = [
        [
            "Номер",
            "Дата",
            "Тип",
            "Клиент",
            "Телефон",
            "Email",
            "Получение",
            "Адрес",
            "Состав",
            "Сумма, ₽",
            "Скидка, %",
            "Итого, ₽",
            "Статус",
            "Оплата",
            "Комментарий",
            "Организация",
            "Участников",
            "Промокод",
            "Скидка офиса, %",
            "Причина скидки офиса",
        ]
    ]
    for order in orders:
        items = "; ".join(
            f"{item['title']}{(' (' + item['variant_sku'] + ')' if item.get('variant_sku') else '')} ×{item['qty']}"
            for item in order["items_json"] or []
        )
        payment = {"succeeded": "Оплачено", "canceled": "Отменена", "review": "ТРЕБУЕТ СВЕРКИ ПЛАТЕЖА"}.get(
            order["payment_status"], ""
        )

        def rubles(value):
            value = value or 0
            return f"{value // 100},{value % 100:02d}"

        rows.append(
            [
                order["number"],
                parse_date(order["created_at"]).astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S")
                if order["created_at"]
                else "",
                types.get(order["type"], order["type"]),
                order["contact_fio"],
                order["contact_phone"],
                order["contact_email"],
                fulfillment_label(order),
                order["address"],
                items,
                rubles(order["subtotal"]),
                applied_discount(order),
                rubles(order["total_estimate"]),
                STATUS_LABELS.get(order["status"], order["status"]),
                payment,
                order["comment"],
                order["organization"],
                order["participants"],
                order["promo_code"],
                order["office_discount"],
                order["office_reason"],
            ]
        )
    await audit(request, "orders.export", actor="admin:" + admin["userId"], detail={"count": len(orders)})
    return csv_response(rows, "orders")
