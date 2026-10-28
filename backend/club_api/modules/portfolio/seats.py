from club_api.core.errors import ApiError

SEATED_STATUSES = ("confirmed", "done")
TAKEN_SQL = "SELECT COALESCE(SUM(participants),0) AS taken FROM orders WHERE {scope} AND status IN (%s,%s)"
CAPACITY_SQL = "SELECT capacity FROM {table} WHERE id=%s FOR UPDATE"


class SeatPool:
    def __init__(self, table, scope):
        self.taken_sql = TAKEN_SQL.format(scope=scope)
        self.capacity_sql = CAPACITY_SQL.format(table=table)

    async def taken(self, connection, id):
        row = await (await connection.execute(self.taken_sql, (id, *SEATED_STATUSES))).fetchone()
        return int(row["taken"])

    async def free(self, connection, row):
        if row["capacity"] is None:
            return None
        return max(0, row["capacity"] - await self.taken(connection, row["id"]))

    async def require_room(self, connection, id, participants):
        row = await (await connection.execute(self.capacity_sql, (id,))).fetchone()
        if not row or row["capacity"] is None:
            return
        taken = await self.taken(connection, id)
        if taken + participants > row["capacity"]:
            raise ApiError(409, f"Мест не осталось: подтверждено {taken} из {row['capacity']}.")


PROGRAM_SEATS = SeatPool("programs", "program_id=%s AND cohort_id IS NULL")
COHORT_SEATS = SeatPool("program_cohorts", "cohort_id=%s")


def seat_pool(order):
    return (COHORT_SEATS, order["cohort_id"]) if order["cohort_id"] else (PROGRAM_SEATS, order["program_id"])


async def require_free_seats(connection, order, status):
    if status not in SEATED_STATUSES or order["status"] in SEATED_STATUSES or not order["program_id"]:
        return
    pool, id = seat_pool(order)
    await pool.require_room(connection, id, order["participants"])
