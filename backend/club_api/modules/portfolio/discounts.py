from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.core.views import api_view, defer, parse_body
from club_api.domain import applied_discount, order_totals, rubles
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin
from club_api.modules.notifications.mail import club_subject
from club_api.modules.portfolio.orders import CONTACT_LINE, locked_order, reachable, require_open
from club_api.observability.audit import audit

MAX_OFFICE_DISCOUNT = 100
MIN_REASON_LENGTH = 3
MAX_REASON_LENGTH = 300


class DiscountBody(Body):
    discount: int | None = Field(ge=0, le=MAX_OFFICE_DISCOUNT)
    reason: str | None = Field(default=None, max_length=MAX_REASON_LENGTH)


def office_change(body):
    if body.discount is None:
        return {"office_discount": None, "office_reason": None}
    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LENGTH:
        raise ApiError(400, "Укажите причину скидки.")
    return {"office_discount": body.discount, "office_reason": reason}


def discount_line(order):
    if order["office_discount"] is not None:
        return f"индивидуальная скидка {order['office_discount']}%"
    if order["member_discount"]:
        return f"индивидуальная скидка отменена, действует скидка {order['member_discount']}%"
    return "индивидуальная скидка отменена"


async def notify_discount(state, order, total):
    if not reachable(order):
        return
    await state.notifications.mail_applicant(
        order["contact_email"],
        club_subject(f"Сумма по заявке {order['number']} изменена"),
        f"Здравствуйте, {order['contact_fio']}!\n\nПо Вашей заявке {order['number']} изменена сумма, "
        f"{discount_line(order)}.\nПредварительная сумма {rubles(total)}.\n\n" + CONTACT_LINE,
    )


@api_view(body=DiscountBody, permission=require_admin)
async def set_discount(request: HttpRequest, id: str):
    admin = await require_admin(request)
    change = office_change(parse_body(request, DiscountBody))
    state = request.services
    async with state.database.transaction() as connection:
        order = await locked_order(state, guid(id), connection)
        require_open(order, "скидку")
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
