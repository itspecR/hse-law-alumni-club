from django.http import HttpRequest
from pydantic import field_validator

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.core.views import api_view, defer, parse_body
from club_api.domain import applied_discount, order_totals, rubles
from club_api.modules.auth.routes import Body
from club_api.modules.auth.service import require_admin
from club_api.modules.portfolio.cohorts import canonical_id, cohort_price
from club_api.modules.portfolio.dates import display_date
from club_api.modules.portfolio.orders import COHORT_USE, locked_order, reachable, require_open
from club_api.modules.portfolio.seats import COHORT_SEATS, SEATED_STATUSES
from club_api.observability.audit import audit

TARGET_FIELDS = ("id", "program_id", "starts_on", "price")


class MoveBody(Body):
    cohort_id: str

    @field_validator("cohort_id")
    @classmethod
    def valid_cohort(cls, value):
        return canonical_id(value)


async def target_cohort(state, order, id, connection):
    await COHORT_USE.lock(connection, id)
    rows = await state.store.read(
        "program_cohorts",
        filters={"id": {"_eq": id}, "program_id": {"_eq": order["program_id"]}},
        fields=TARGET_FIELDS,
        limit=1,
        connection=connection,
    )
    if not rows:
        raise ApiError(400, "Поток не найден в этой программе. Обновите страницу")
    if rows[0]["id"] == order["cohort_id"]:
        raise ApiError(409, "Заявка уже в этом потоке")
    return rows[0]


async def program_of(state, order, connection):
    rows = await state.store.read(
        "programs", filters={"id": {"_eq": order["program_id"]}}, fields=("price",), limit=1, connection=connection
    )
    return rows[0]


def repriced(order, program, cohort):
    price = cohort_price(program, cohort)
    items = [{**item, "price": price} for item in order["items_json"]]
    totals = order_totals(items, applied_discount(order))
    return {
        "cohort_id": cohort["id"],
        "items_json": items,
        "subtotal": totals["subtotal"],
        "total_estimate": totals["total"],
    }


async def notify_move(state, order, cohort, total):
    if not reachable(order):
        return
    await state.notifications.mail_applicant(
        order["contact_email"],
        f"Заявка {order['number']}: перенос в другой поток – Клуб выпускников факультета права",
        f"Здравствуйте, {order['contact_fio']}!\n\nЗаявка {order['number']} перенесена в поток "
        f"со стартом {display_date(cohort['starts_on'])}.\nСумма (справочно): {rubles(total)}.\n\n"
        "Менеджер учебного офиса свяжется с вами, чтобы согласовать договор и оплату.",
    )


@api_view(body=MoveBody, permission=require_admin)
async def move_order(request: HttpRequest, id: str):
    admin = await require_admin(request)
    body = parse_body(request, MoveBody)
    state = request.services
    async with state.database.transaction() as connection:
        order = await locked_order(state, guid(id), connection)
        require_open(order, "поток")
        cohort = await target_cohort(state, order, body.cohort_id, connection)
        if order["status"] in SEATED_STATUSES:
            await COHORT_SEATS.require_room(connection, cohort["id"], order["participants"])
        change = repriced(order, await program_of(state, order, connection), cohort)
        await state.store.update("orders", change, id=order["id"], connection=connection)
    await audit(
        request,
        "order.cohort",
        actor="admin:" + admin["userId"],
        subject="order:" + order["number"],
        detail={"from": order["cohort_id"], "to": cohort["id"], "total": change["total_estimate"]},
    )
    defer(request, notify_move, state, order, cohort, change["total_estimate"])
    return {
        "ok": True,
        "starts_on": cohort["starts_on"],
        **{key: change[key] for key in ("cohort_id", "subtotal", "total_estimate")},
    }
