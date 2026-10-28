from club_api.core.errors import ApiError
from club_api.domain import PORTFOLIO_CATALOG

OPEN_STATUSES = ("new", "in_progress", "confirmed")
LOCKED_FIELDS = (
    "id",
    "number",
    "type",
    "status",
    "items_json",
    "member_discount",
    "office_discount",
    "contact_email",
    "contact_fio",
    "program_id",
    "cohort_id",
    "participants",
)


def require_open(order, subject):
    if order["type"] != PORTFOLIO_CATALOG:
        raise ApiError(400, f"{subject.capitalize()} можно изменить только в заявке на программу клуба")
    if order["status"] not in OPEN_STATUSES:
        raise ApiError(409, f"Заявка завершена или отменена – {subject} изменить нельзя")


async def locked_order(state, id, connection):
    await connection.execute("SELECT id FROM orders WHERE id=%s FOR UPDATE", (id,))
    rows = await state.store.read(
        "orders", filters={"id": {"_eq": id}}, fields=LOCKED_FIELDS, limit=1, connection=connection
    )
    if not rows:
        raise ApiError(404, "Заявка не найдена")
    return rows[0]


def reachable(order):
    return bool(order["contact_email"]) and order["contact_email"] != "-"
