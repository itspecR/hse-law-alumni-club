from club_api.core.errors import ApiError
from club_api.domain import PORTFOLIO_CATALOG

OPEN_STATUSES = ("new", "in_progress", "confirmed")
LOCK_SQL = "SELECT id FROM {table} WHERE id=%s FOR UPDATE"
USED_SQL = "SELECT id FROM orders WHERE {column}=%s LIMIT 1"
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


class OrderUse:
    def __init__(self, table, column, missing, used):
        self.lock_sql = LOCK_SQL.format(table=table)
        self.used_sql = USED_SQL.format(column=column)
        self.missing, self.used_message = missing, used

    async def lock(self, connection, id):
        return await (await connection.execute(self.lock_sql, (id,))).fetchone()

    async def used(self, connection, id):
        return bool(await (await connection.execute(self.used_sql, (id,))).fetchone())

    async def guard(self, connection, id):
        if not await self.lock(connection, id):
            raise ApiError(404, self.missing)
        if await self.used(connection, id):
            raise ApiError(409, self.used_message)


COHORT_USE = OrderUse(
    "program_cohorts", "cohort_id", "Поток не найден", "В потоке есть заявки: закройте набор вместо удаления"
)
PROGRAM_USE = OrderUse(
    "programs", "program_id", "Программа не найдена", "У программы есть заявки: переведите её в архив вместо удаления"
)
