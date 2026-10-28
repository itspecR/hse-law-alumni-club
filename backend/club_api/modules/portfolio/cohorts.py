from typing import Literal

from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.db.queries import Query
from club_api.modules.auth.routes import Body
from club_api.modules.catalog.scope import PORTFOLIO_PROGRAMS
from club_api.modules.checkout.payments import SAFE_INTEGER_MAX
from club_api.modules.content.admin import content_crud
from club_api.modules.portfolio.admin import MAX_CAPACITY, canonical_ids
from club_api.modules.portfolio.dates import iso_date, moscow_today
from club_api.modules.portfolio.orders import COHORT_USE
from club_api.modules.portfolio.relations import require_existing
from club_api.modules.portfolio.seats import COHORT_SEATS, SEATED_STATUSES

OPEN_ENROLLMENT = "actual"
COHORT_FIELDS = ("id", "program_id", "starts_on", "price", "capacity", "enrollment", "created_at")
PUBLIC_FIELDS = ("id", "program_id", "starts_on", "price", "capacity")
SHARE_SQL = Query(
    "SELECT id FROM program_cohorts WHERE id=%s FOR SHARE",
    "SELECT id FROM program_cohorts WHERE id=%s LOCK IN SHARE MODE",
)
CLOSED_MESSAGE = "Набор в этот поток закрыт. Обновите страницу и выберите другой поток."


def canonical_id(value):
    return canonical_ids([value])[0] if value else None


class CohortBody(Body):
    program_id: str
    starts_on: str = Field(max_length=10)
    price: int | None = Field(default=None, ge=0, le=SAFE_INTEGER_MAX)
    capacity: int | None = Field(default=None, ge=1, le=MAX_CAPACITY)
    enrollment: Literal["actual", "nonactual"] = OPEN_ENROLLMENT

    @field_validator("program_id")
    @classmethod
    def valid_program(cls, value):
        return canonical_id(value)

    @field_validator("starts_on")
    @classmethod
    def valid_date(cls, value):
        return iso_date(value)


def cohort_price(program, cohort):
    if cohort and cohort["price"] is not None:
        return cohort["price"]
    return program["price"] or 0


async def open_cohorts(state, program_ids, connection=None):
    return await state.store.read(
        "program_cohorts",
        filters={
            "program_id": {"_in": program_ids},
            "enrollment": {"_eq": OPEN_ENROLLMENT},
            "starts_on": {"_gte": moscow_today()},
        },
        fields=PUBLIC_FIELDS,
        sort=("starts_on",),
        limit=-1,
        connection=connection,
    )


async def public_cohorts(state, program, connection):
    return [
        {**row, "price": cohort_price(program, row), "seats_left": await COHORT_SEATS.free(connection, row)}
        for row in await open_cohorts(state, [program["id"]], connection)
    ]


async def cohorts_by_program(state, programs):
    owners, grouped = {program["id"]: program for program in programs}, {}
    for row in await open_cohorts(state, list(owners)):
        price = cohort_price(owners[row["program_id"]], row)
        grouped.setdefault(row["program_id"], []).append({"starts_on": row["starts_on"], "price": price})
    return grouped


async def with_starts(state, orders):
    ids = list({order["cohort_id"] for order in orders if order["cohort_id"]})
    rows = await state.store.read("program_cohorts", filters={"id": {"_in": ids}}, fields=("id", "starts_on"), limit=-1)
    starts = {row["id"]: row["starts_on"] for row in rows}
    return [{**order, "starts_on": starts.get(order["cohort_id"])} for order in orders]


async def chosen_cohort(state, program_id, id, connection=None, *, lock=False):
    if id and lock:
        await connection.execute(SHARE_SQL, (id,))
    cohorts = await open_cohorts(state, [program_id], connection)
    if id is None:
        if cohorts:
            raise ApiError(400, "Выберите поток.")
        return None
    cohort = next((row for row in cohorts if row["id"] == id), None)
    if cohort is None:
        raise ApiError(400, CLOSED_MESSAGE)
    return cohort


async def seated(state, ids):
    if not ids:
        return {}
    rows = await state.store.aggregate(
        "orders",
        filters={"cohort_id": {"_in": ids}, "status": {"_in": list(SEATED_STATUSES)}},
        group=("cohort_id",),
        sum_field="participants",
    )
    return {row["cohort_id"]: int(row["sum"]["participants"] or 0) for row in rows}


def is_open(cohort, today):
    return cohort["enrollment"] == OPEN_ENROLLMENT and cohort["starts_on"] >= today.isoformat()


class CohortRules:
    def split(self, data):
        return dict(data), None

    async def prepare(self, state, data, links, id, connection):
        if id:
            await COHORT_USE.lock(connection, id)
        current = await state.store.read("program_cohorts", filters={"id": {"_eq": id}}, limit=1, connection=connection)
        if id and not current:
            raise ApiError(404, "Поток не найден")
        if "program_id" not in data:
            return data
        await require_existing(
            state,
            "programs",
            [data["program_id"]],
            PORTFOLIO_PROGRAMS,
            "Программа не найдена. Обновите страницу и выберите программу заново",
            connection,
        )
        if current and current[0]["program_id"] != data["program_id"] and await COHORT_USE.used(connection, id):
            raise ApiError(409, "В потоке есть заявки: программу потока изменить нельзя")
        return data

    async def attach(self, state, rows):
        taken, today = await seated(state, [row["id"] for row in rows]), moscow_today()
        return [{**row, "taken": taken.get(row["id"], 0), "open": is_open(row, today)} for row in rows]


urlpatterns = content_crud(
    "/admin/cohorts",
    "program_cohorts",
    CohortBody,
    relation=CohortRules(),
    guard=COHORT_USE.guard,
    sort=("starts_on",),
    fields=COHORT_FIELDS,
    subject="cohort",
)
