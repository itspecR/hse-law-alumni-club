from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.db.store import normalize
from club_api.modules.auth.routes import Body
from club_api.modules.catalog.scope import PORTFOLIO_PROGRAMS
from club_api.modules.content.admin import content_crud
from club_api.modules.portfolio.admin import canonical_ids

PROMO_MAX_PERCENT = 50
DEFAULT_PER_PERSON = 1
MAX_PROMO_LIMIT = 100000
MAX_PROMO_PROGRAMS = 50
CODE_PATTERN = r"^[A-Za-z0-9-]{3,32}$"
PROMO_PROGRAMS = "promo_code_programs"
RELEASED_STATUSES = ("canceled", "expired")
MOSCOW = ZoneInfo("Europe/Moscow")
PROMO_FIELDS = (
    "id",
    "code",
    "percent",
    "starts_on",
    "ends_on",
    "total_limit",
    "per_person_limit",
    "all_programs",
    "status",
    "note",
    "created_at",
)
PROMO_SQL = {
    False: "SELECT * FROM promo_codes WHERE code=%s",
    True: "SELECT * FROM promo_codes WHERE code=%s FOR UPDATE",
}
USES_SQL = "SELECT COUNT(*) AS used FROM orders WHERE promo_id=%s AND status NOT IN (%s,%s)"
PERSON_USES_SQL = USES_SQL + " AND LOWER(contact_email)=LOWER(%s)"


def iso_date(value):
    date.fromisoformat(value)
    return value


class PromoBody(Body):
    code: str = Field(pattern=CODE_PATTERN)
    percent: int = Field(ge=1, le=PROMO_MAX_PERCENT)
    starts_on: str = Field(max_length=10)
    ends_on: str = Field(max_length=10)
    total_limit: int | None = Field(default=None, ge=1, le=MAX_PROMO_LIMIT)
    per_person_limit: int | None = Field(default=DEFAULT_PER_PERSON, ge=1, le=MAX_PROMO_LIMIT)
    all_programs: bool = True
    program_ids: list[str] = Field(default_factory=list, max_length=MAX_PROMO_PROGRAMS)
    status: Literal["active", "disabled"] = "active"
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("code")
    @classmethod
    def upper_code(cls, value):
        return value.upper()

    @field_validator("starts_on", "ends_on")
    @classmethod
    def valid_date(cls, value):
        return iso_date(value)

    @field_validator("program_ids")
    @classmethod
    def unique_programs(cls, value):
        return canonical_ids(value)


async def require_unique_code(state, code, id, connection):
    filters = {"code": {"_eq": code}, **({"id": {"_neq": id}} if id else {})}
    if await state.store.read("promo_codes", filters=filters, fields=("id",), limit=1, connection=connection):
        raise ApiError(409, "Такой промокод уже есть")


async def require_programs(state, ids, connection):
    rows = await state.store.read(
        "programs", filters={"id": {"_in": ids}, **PORTFOLIO_PROGRAMS}, fields=("id",), limit=-1, connection=connection
    )
    if len(rows) != len(ids):
        raise ApiError(400, "Программа не найдена. Обновите страницу и выберите программы заново")


async def linked_programs(state, promo_ids, connection=None):
    if not promo_ids:
        return {}
    rows = await state.store.read(
        PROMO_PROGRAMS,
        filters={"promo_id": {"_in": promo_ids}},
        fields=("promo_id", "program_id"),
        limit=-1,
        connection=connection,
    )
    result = {}
    for row in rows:
        result.setdefault(row["promo_id"], []).append(row["program_id"])
    return result


async def promo_uses(state, promo_ids):
    if not promo_ids:
        return {}
    rows = await state.store.aggregate(
        "orders",
        filters={"promo_id": {"_in": promo_ids}, "status": {"_nin": list(RELEASED_STATUSES)}},
        group=("promo_id",),
    )
    return {row["promo_id"]: int(row["count"]) for row in rows}


class PromoRules:
    def split(self, data):
        data = dict(data)
        return data, data.pop("program_ids", None)

    async def prepare(self, state, data, program_ids, id, connection):
        current = await state.store.read("promo_codes", filters={"id": {"_eq": id}}, limit=1, connection=connection)
        merged = {**(current[0] if current else {}), **data}
        if "code" in data:
            await require_unique_code(state, data["code"], id, connection)
        if date.fromisoformat(merged["ends_on"]) < date.fromisoformat(merged["starts_on"]):
            raise ApiError(400, "Дата окончания раньше даты начала")
        programs = (
            program_ids if program_ids is not None else (await linked_programs(state, [id], connection)).get(id, [])
        )
        if not merged.get("all_programs", True) and not programs:
            raise ApiError(400, "Выберите программы или отметьте «Все программы портфеля»")
        if program_ids:
            await require_programs(state, program_ids, connection)
        return data

    async def save(self, state, promo_id, program_ids, connection):
        await state.store.delete(PROMO_PROGRAMS, filters={"promo_id": {"_eq": promo_id}}, connection=connection)
        for program_id in program_ids:
            await state.store.create(
                PROMO_PROGRAMS, {"promo_id": promo_id, "program_id": program_id}, connection=connection
            )

    async def attach(self, state, rows):
        ids = [row["id"] for row in rows]
        links, uses = await linked_programs(state, ids), await promo_uses(state, ids)
        return [{**row, "program_ids": links.get(row["id"], []), "used": uses.get(row["id"], 0)} for row in rows]


async def count(connection, sql, params):
    return int((await (await connection.execute(sql, params)).fetchone())["used"])


def require_window(promo):
    today = datetime.now(MOSCOW).date()
    if today < date.fromisoformat(promo["starts_on"]):
        raise ApiError(400, "Промокод ещё не действует")
    if today > date.fromisoformat(promo["ends_on"]):
        raise ApiError(400, "Срок действия промокода истёк")


async def require_program(connection, promo, program_id):
    if promo["all_programs"]:
        return
    cursor = await connection.execute(
        "SELECT id FROM promo_code_programs WHERE promo_id=%s AND program_id=%s", (promo["id"], program_id)
    )
    if not await cursor.fetchone():
        raise ApiError(400, "Промокод не действует на эту программу")


async def require_uses(connection, promo, email):
    released = (promo["id"], *RELEASED_STATUSES)
    if promo["total_limit"] and await count(connection, USES_SQL, released) >= promo["total_limit"]:
        raise ApiError(400, "Лимит промокода исчерпан")
    if promo["per_person_limit"] and (
        await count(connection, PERSON_USES_SQL, (*released, email)) >= promo["per_person_limit"]
    ):
        raise ApiError(400, "Вы уже использовали этот промокод")


async def valid_promo(connection, code, program_id, email, *, lock):
    promo = normalize(await (await connection.execute(PROMO_SQL[lock], (code,))).fetchone())
    if not promo or promo["status"] != "active":
        raise ApiError(400, "Промокод не найден")
    require_window(promo)
    await require_program(connection, promo, program_id)
    await require_uses(connection, promo, email)
    return promo


urlpatterns = content_crud(
    "/admin/promo-codes",
    "promo_codes",
    PromoBody,
    relation=PromoRules(),
    sort=("-created_at",),
    fields=PROMO_FIELDS,
    subject="promo",
)
