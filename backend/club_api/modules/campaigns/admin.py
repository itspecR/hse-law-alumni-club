from typing import Annotated, Literal

from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.requisites import FORBIDDEN, normalized_text
from club_api.core.schemas import SAFE_INTEGER_MAX, Body
from club_api.modules.campaigns.service import CAMPAIGN_TRAINERS, donated, progress
from club_api.modules.campaigns.store import CAMPAIGN_TABLE
from club_api.modules.content.admin import content_crud
from club_api.modules.donations.routes import DONATION_MAX_RUB, DONATION_MIN_RUB, KOPECKS
from club_api.modules.portfolio.admin import (
    MAX_ITEM_LENGTH,
    MAX_LIST_ITEMS,
    MAX_MODULES,
    MAX_PROGRAM_TRAINERS,
    Item,
    TrainerLinks,
    canonical_ids,
)

RESERVED_SLUGS = frozenset(("offer", "consent", "thanks"))
SLUG_PATTERN = "^[a-z0-9]+(?:-[a-z0-9]+)*$"
SLUG_MIN, SLUG_MAX = 2, 64
LINE_MAX = 255
LEDE_MAX = 600
NOTE_MAX = 1000
TEXT_MAX = 3000
LETTER_MAX = 5000
PURPOSE_MAX = 110
HIGHLIGHT_VALUE_MAX = 20
MAX_HIGHLIGHTS = 4
MAX_STEPS = 6
MAX_AMOUNTS = 6
MAX_QUESTIONS = 20
AMOUNT_MIN, AMOUNT_MAX = DONATION_MIN_RUB * KOPECKS, DONATION_MAX_RUB * KOPECKS
SLUG_RESERVED = "Этот адрес занят служебной страницей. Выберите другой."
SLUG_TAKEN = "Сбор с таким адресом уже есть."
HAS_DONATIONS = "У сбора есть пожертвования. Вместо удаления переведите его в архив."
PURPOSE_INVALID = "В назначении платежа допустимы только печатные знаки без вертикальной черты."
AMOUNTS_INVALID = f"Суммы в форме должны различаться и быть от {DONATION_MIN_RUB} до {DONATION_MAX_RUB} ₽."
CAMPAIGN_FIELDS = (
    "id",
    "slug",
    "status",
    "title",
    "subtitle",
    "lede",
    "date_text",
    "time_text",
    "place_text",
    "duration_text",
    "goal_title",
    "goal_text",
    "goal_amount",
    "extra_amount",
    "invite_threshold",
    "amounts",
    "about_title",
    "about",
    "highlights",
    "program_title",
    "modules",
    "feature_title",
    "feature_note",
    "feature_text",
    "results_title",
    "results",
    "steps",
    "faq",
    "note",
    "payment_purpose",
    "invite_subject",
    "invite_body",
    "cover",
    "created_at",
)

Line = Annotated[str | None, Field(default=None, max_length=LINE_MAX)]
Text = Annotated[str | None, Field(default=None, max_length=TEXT_MAX)]
Money = Annotated[int, Field(default=0, ge=0, le=SAFE_INTEGER_MAX)]


class HighlightBody(Body):
    value: str = Field(min_length=1, max_length=HIGHLIGHT_VALUE_MAX)
    label: str = Field(min_length=1, max_length=LINE_MAX)


class DayBlockBody(Body):
    title: str = Field(min_length=1, max_length=MAX_ITEM_LENGTH)
    time: Line
    note: Line
    points: list[Item] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)


class StepBody(Body):
    title: str = Field(min_length=1, max_length=LINE_MAX)
    text: str = Field(min_length=1, max_length=NOTE_MAX)


class QuestionBody(Body):
    question: str = Field(min_length=1, max_length=MAX_ITEM_LENGTH)
    answer: str = Field(min_length=1, max_length=TEXT_MAX)


class CampaignBody(Body):
    slug: str = Field(min_length=SLUG_MIN, max_length=SLUG_MAX, pattern=SLUG_PATTERN)
    title: str = Field(min_length=3, max_length=LINE_MAX)
    subtitle: Line
    lede: str | None = Field(default=None, max_length=LEDE_MAX)
    date_text: Line
    time_text: Line
    place_text: Line
    duration_text: Line
    goal_title: Line
    goal_text: Text
    goal_amount: Money
    extra_amount: Money
    invite_threshold: int = Field(default=0, ge=0, le=AMOUNT_MAX)
    amounts: list[int] = Field(default_factory=list, max_length=MAX_AMOUNTS)
    about_title: Line
    about: Text
    highlights: list[HighlightBody] = Field(default_factory=list, max_length=MAX_HIGHLIGHTS)
    program_title: Line
    modules: list[DayBlockBody] = Field(default_factory=list, max_length=MAX_MODULES)
    feature_title: Line
    feature_note: Line
    feature_text: Text
    results_title: Line
    results: list[Item] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    steps: list[StepBody] = Field(default_factory=list, max_length=MAX_STEPS)
    faq: list[QuestionBody] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    note: str | None = Field(default=None, max_length=NOTE_MAX)
    payment_purpose: str | None = Field(default=None, max_length=PURPOSE_MAX)
    invite_subject: Line
    invite_body: str | None = Field(default=None, max_length=LETTER_MAX)
    cover: Line
    trainer_ids: list[str] = Field(default_factory=list, max_length=MAX_PROGRAM_TRAINERS)
    status: Literal["draft", "published", "archived"] = "draft"

    @field_validator("amounts")
    @classmethod
    def distinct_amounts(cls, value):
        if len(set(value)) != len(value) or any(not AMOUNT_MIN <= amount <= AMOUNT_MAX for amount in value):
            raise ValueError(AMOUNTS_INVALID)
        return sorted(value)

    @field_validator("payment_purpose")
    @classmethod
    def printable_purpose(cls, value):
        purpose = normalized_text(value or "")
        if FORBIDDEN in purpose or not purpose.isprintable():
            raise ValueError(PURPOSE_INVALID)
        return purpose or None

    @field_validator("trainer_ids")
    @classmethod
    def unique_trainers(cls, value):
        return canonical_ids(value)


async def require_free_slug(state, slug, id, connection):
    if not slug:
        return
    if slug in RESERVED_SLUGS:
        raise ApiError(400, SLUG_RESERVED)
    rows = await state.store.read(
        CAMPAIGN_TABLE, filters={"slug": {"_eq": slug}}, fields=("id",), limit=1, connection=connection
    )
    if rows and rows[0]["id"] != id:
        raise ApiError(409, SLUG_TAKEN)


async def keep_donations(connection, id):
    cursor = await connection.execute("SELECT id FROM club_donations WHERE campaign_id=%s LIMIT 1", (id,))
    if await cursor.fetchone():
        raise ApiError(409, HAS_DONATIONS)


class CampaignLinks(TrainerLinks):
    async def prepare(self, state, data, trainer_ids, id, connection):
        data = await super().prepare(state, data, trainer_ids, id, connection)
        await require_free_slug(state, data.get("slug"), id, connection)
        return data

    async def attach(self, state, rows):
        rows = await super().attach(state, rows)
        return [{**row, **progress(row, await donated(state, row["id"]))} for row in rows]


urlpatterns = content_crud(
    "/admin/campaigns",
    CAMPAIGN_TABLE,
    CampaignBody,
    relation=CampaignLinks(CAMPAIGN_TRAINERS),
    guard=keep_donations,
    sort=("-created_at", "slug"),
    fields=CAMPAIGN_FIELDS,
    subject="campaign",
)
