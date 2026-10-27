from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.domain import PORTFOLIO_CATALOG
from club_api.modules.auth.routes import Body
from club_api.modules.checkout.payments import SAFE_INTEGER_MAX
from club_api.modules.content.admin import content_crud

DEFAULT_DOCUMENT = "Сертификат об обучении"
MAX_LIST_ITEMS = 20
MAX_ITEM_LENGTH = 300
MAX_PROGRAM_TRAINERS = 10
MAX_CAPACITY = 1000
MAX_BIO_LENGTH = 5000
LINKS = "program_trainers"
PORTFOLIO_FIELDS = (
    "id",
    "slug",
    "title",
    "direction",
    "format",
    "duration",
    "price",
    "status",
    "enrollment",
    "dates",
    "document",
    "description",
    "cover",
    "audience",
    "competencies",
    "communication",
    "capacity",
    "seats_taken",
)
TRAINER_FIELDS = ("id", "fio", "photo", "bio", "status", "created_at")

Item = Annotated[str, Field(min_length=1, max_length=MAX_ITEM_LENGTH)]


def canonical_ids(values):
    try:
        return list(dict.fromkeys(str(UUID(value)) for value in values))
    except ValueError as error:
        raise ValueError("Некорректный идентификатор тренера") from error


class TrainerBody(Body):
    fio: str = Field(min_length=3, max_length=255)
    photo: str | None = Field(default=None, max_length=255)
    bio: str | None = Field(default=None, max_length=MAX_BIO_LENGTH)
    status: Literal["draft", "published", "archived"] = "published"


class PortfolioBody(Body):
    title: str = Field(min_length=3, max_length=255)
    direction: str | None = Field(default=None, max_length=255)
    format: Literal["online", "offline", "blended"]
    duration: str = Field(min_length=1, max_length=255)
    price: int = Field(ge=0, le=SAFE_INTEGER_MAX)
    description: str | None = None
    cover: str | None = Field(default=None, max_length=255)
    start: str | None = Field(default=None, max_length=255)
    document: str = Field(default=DEFAULT_DOCUMENT, min_length=2, max_length=255)
    audience: list[Item] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    competencies: list[Item] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    communication: list[Item] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    capacity: int | None = Field(default=None, ge=1, le=MAX_CAPACITY)
    enrollment: Literal["actual", "nonactual"] = "actual"
    trainer_ids: list[str] = Field(default_factory=list, max_length=MAX_PROGRAM_TRAINERS)
    status: Literal["draft", "published", "archived"] = "published"

    @field_validator("document", mode="before")
    @classmethod
    def default_document(cls, value):
        return DEFAULT_DOCUMENT if isinstance(value, str) and not value.strip() else value

    @field_validator("trainer_ids")
    @classmethod
    def unique_trainers(cls, value):
        return canonical_ids(value)


async def require_trainers(state, ids, connection):
    if not ids:
        return
    rows = await state.store.read(
        "trainers", filters={"id": {"_in": ids}}, fields=("id",), limit=-1, connection=connection
    )
    if len(rows) != len(ids):
        raise ApiError(400, "Тренер не найден. Обновите страницу и выберите тренеров заново")


class TrainerLinks:
    def split(self, data):
        data = dict(data)
        return data, data.pop("trainer_ids", None)

    async def save(self, state, program_id, trainer_ids, connection):
        await require_trainers(state, trainer_ids, connection)
        await state.store.delete(LINKS, filters={"program_id": {"_eq": program_id}}, connection=connection)
        for sort, trainer_id in enumerate(trainer_ids):
            await state.store.create(
                LINKS, {"program_id": program_id, "trainer_id": trainer_id, "sort": sort}, connection=connection
            )

    async def attach(self, state, rows):
        links = await program_links(state, [row["id"] for row in rows])
        return [{**row, "trainer_ids": links.get(row["id"], [])} for row in rows]


async def program_links(state, program_ids):
    if not program_ids:
        return {}
    rows = await state.store.read(
        LINKS,
        filters={"program_id": {"_in": program_ids}},
        fields=("program_id", "trainer_id"),
        sort=("sort",),
        limit=-1,
    )
    result = {}
    for row in rows:
        result.setdefault(row["program_id"], []).append(row["trainer_id"])
    return result


urlpatterns = content_crud(
    "/admin/trainers", "trainers", TrainerBody, sort=("fio",), fields=TRAINER_FIELDS, subject="trainer"
)
urlpatterns += content_crud(
    "/admin/portfolio",
    "programs",
    PortfolioBody,
    scope={"catalog": PORTFOLIO_CATALOG},
    relation=TrainerLinks(),
    sort=("title",),
    fields=PORTFOLIO_FIELDS,
    subject="portfolio",
)
