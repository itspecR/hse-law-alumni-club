from club_api.core.errors import ApiError

SEATED_STATUSES = ("confirmed", "done")
TAKEN_SQL = "SELECT COALESCE(SUM(participants),0) AS taken FROM orders WHERE program_id=%s AND status IN (%s,%s)"


async def taken_seats(connection, program_id):
    row = await (await connection.execute(TAKEN_SQL, (program_id, *SEATED_STATUSES))).fetchone()
    return int(row["taken"])


async def free_seats(state, program):
    if program["capacity"] is None:
        return None
    async with state.database.connection() as connection:
        return max(0, program["capacity"] - await taken_seats(connection, program["id"]))


async def require_free_seats(connection, order, status):
    if status not in SEATED_STATUSES or order["status"] in SEATED_STATUSES or not order["program_id"]:
        return
    cursor = await connection.execute("SELECT capacity FROM programs WHERE id=%s FOR UPDATE", (order["program_id"],))
    program = await cursor.fetchone()
    if not program or program["capacity"] is None:
        return
    taken = await taken_seats(connection, order["program_id"])
    if taken + order["participants"] > program["capacity"]:
        raise ApiError(409, f"Мест не осталось: подтверждено {taken} из {program['capacity']}")
