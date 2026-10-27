from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.core.views import api_view, defer, parse_body
from club_api.domain import PORTFOLIO_CATALOG, applied_discount, order_totals, rubles
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin
from club_api.observability.audit import audit

MAX_OFFICE_DISCOUNT = 100
MIN_REASON_LENGTH = 3
MAX_REASON_LENGTH = 300
OPEN_STATUSES = ("new", "in_progress", "confirmed")
DISCOUNT_FIELDS = (
    "id",
    "number",
    "type",
    "status",
    "items_json",
    "member_discount",
    "office_discount",
    "contact_email",
    "contact_fio",
)


class DiscountBody(Body):
    discount: int | None = Field(ge=0, le=MAX_OFFICE_DISCOUNT)
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)


def office_change(body):
    if body.discount is None:
        return {"office_discount": None, "office_reason": None}
    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise ApiError(400, "Укажите причину скидки")
    return {"office_discount": body.discount, "office_reason": reason}


def require_open(order):
    if order["type"] != PORTFOLIO_CATALOG:
        raise ApiError(400, "Скидку офиса можно задать только в заявке на программу клуба")
    if order["status"] not in OPEN_STATUSES:
        raise ApiError(409, "Заявка завершена или отменена – скидку изменить нельзя")


async def locked_order(state, id, connection):
    await connection.execute("SELECT id FROM orders WHERE id=%s FOR UPDATE", (id,))
    rows = await state.store.read(
        "orders", filters={"id": {"_eq": id}}, fields=DISCOUNT_FIELDS, limit=1, connection=connection
    )
    if not rows:
        raise ApiError(404, "Заявка не найдена")
    return rows[0]


def discount_line(order):
    if order["office_discount"] is not None:
        return f"скидка учебного офиса {order['office_discount']}%"
    if order["member_discount"]:
        return f"скидка учебного офиса отменена, действует скидка {order['member_discount']}%"
    return "скидка учебного офиса отменена"


async def notify_discount(state, order, total):
    if not order["contact_email"] or order["contact_email"] == "-":
        return
    await state.notifications.send_email(
        order["contact_email"],
        f"Заявка {order['number']}: сумма изменена – Клуб выпускников факультета права",
        f"Здравствуйте, {order['contact_fio']}!\n\nПо заявке {order['number']} изменена сумма: "
        f"{discount_line(order)}.\nСумма (справочно): {rubles(total)}.\n\n"
        "Менеджер учебного офиса свяжется с вами, чтобы согласовать договор и оплату.",
    )


@api_view(body=DiscountBody, permission=require_admin)
async def set_discount(request: HttpRequest, id: str):
    admin = await require_admin(request)
    change = office_change(parse_body(request, DiscountBody))
    state = request.services
    async with state.database.transaction() as connection:
        order = await locked_order(state, guid(id), connection)
        require_open(order)
        previous = applied_discount(order)
        current = {**order, **change}
        total = order_totals(order["items_json"], applied_discount(current))["total"]
        await state.store.update("orders", {**change, "total_estimate": total}, id=order["id"], connection=connection)
    await audit(
        request,
        "order.discount",
        actor="admin:" + admin["userId"],
        subject="order:" + order["number"],
        detail={"discount": change["office_discount"], "previous": previous, "total": total},
    )
    if applied_discount(current) != previous:
        defer(request, notify_discount, state, current, total)
    return {"ok": True, "discount": applied_discount(current), **change, "total_estimate": total}
