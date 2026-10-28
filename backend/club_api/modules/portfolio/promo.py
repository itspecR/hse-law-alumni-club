from datetime import date
from typing import Literal

from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.db.store import normalize
from club_api.modules.auth.routes import Body
from club_api.modules.catalog.scope import PORTFOLIO_PROGRAMS
from club_api.modules.content.admin import content_crud
from club_api.modules.portfolio.admin import canonical_ids
from club_api.modules.portfolio.dates import iso_date, moscow_today
from club_api.modules.portfolio.relations import PROMO_PROGRAMS, require_existing

PROMO_MAX_PERCENT = 50
DEFAULT_PER_PERSON = 1
MAX_PROMO_LIMIT = 100000
MAX_PROMO_PROGRAMS = 50
CODE_PATTERN = r"^[A-Za-z0-9-]{3,32}$"
RELEASED_STATUSES = ("canceled", "expired")
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
        if id and not current:
            raise ApiError(404, "Промокод не найден.")
        merged = {**(current[0] if current else {}), **data}
        if "code" in data:
            await require_unique_code(state, data["code"], id, connection)
        if date.fromisoformat(merged["ends_on"]) < date.fromisoformat(merged["starts_on"]):
            raise ApiError(400, "Дата окончания раньше даты начала")
        programs = (
            program_ids
            if program_ids is not None
            else (await PROMO_PROGRAMS.grouped(state, [id], connection)).get(id, [])
        )
        if not merged.get("all_programs", True) and not programs:
            raise ApiError(400, "Выберите программы или отметьте «Все программы портфеля»")
        await require_existing(
            state,
            "programs",
            program_ids,
            PORTFOLIO_PROGRAMS,
            "Программа не найдена. Обновите страницу и выберите программы заново",
            connection,
        )
        return data

    async def save(self, state, promo_id, program_ids, connection):
        await PROMO_PROGRAMS.replace(state, promo_id, program_ids, connection)

    async def attach(self, state, rows):
        ids = [row["id"] for row in rows]
        links, uses = await PROMO_PROGRAMS.grouped(state, ids), await promo_uses(state, ids)
        return [{**row, "program_ids": links.get(row["id"], []), "used": uses.get(row["id"], 0)} for row in rows]


async def count(connection, sql, params):
    return int((await (await connection.execute(sql, params)).fetchone())["used"])


def require_window(promo):
    today = moscow_today()
    if today < date.fromisoformat(promo["starts_on"]):
        raise ApiError(400, "Промокод еще не действует.")
    if today > date.fromisoformat(promo["ends_on"]):
        raise ApiError(400, "Срок действия промокода истек.")


async def require_program(connection, promo, program_id):
    if promo["all_programs"]:
        return
    cursor = await connection.execute(
        "SELECT id FROM promo_code_programs WHERE promo_id=%s AND program_id=%s", (promo["id"], program_id)
    )
    if not await cursor.fetchone():
        raise ApiError(400, "Промокод не действует на эту программу.")


async def require_uses(connection, promo, email):
    released = (promo["id"], *RELEASED_STATUSES)
    if promo["total_limit"] and await count(connection, USES_SQL, released) >= promo["total_limit"]:
        raise ApiError(400, "Промокод использован максимальное число раз.")
    if (
        email is not None
        and promo["per_person_limit"]
        and (await count(connection, PERSON_USES_SQL, (*released, email)) >= promo["per_person_limit"])
    ):
        raise ApiError(400, "Вы уже использовали этот промокод.")


async def valid_promo(connection, code, program_id, email, *, lock):
    promo = normalize(await (await connection.execute(PROMO_SQL[lock], (code,))).fetchone())
    if not promo or promo["status"] != "active":
        raise ApiError(400, "Промокод не найден.")
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
