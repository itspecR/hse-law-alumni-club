from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view
from club_api.modules.catalog.scope import PUBLISHED_PORTFOLIO
from club_api.modules.portfolio.applications import MAX_PARTICIPANTS, MIN_GROUP
from club_api.modules.portfolio.links import published_trainers
from club_api.modules.portfolio.seats import free_seats

CARD_FIELDS = (
    "id",
    "slug",
    "title",
    "direction",
    "format",
    "duration",
    "price",
    "enrollment",
    "dates",
    "document",
    "cover",
    "capacity",
)
DETAIL_FIELDS = (*CARD_FIELDS, "description", "audience", "competencies", "communication")
OPEN_FIRST = ("enrollment", "title")


@api_view
async def programs(request: HttpRequest):
    return await request.services.store.read(
        "programs", filters=PUBLISHED_PORTFOLIO, fields=CARD_FIELDS, sort=OPEN_FIRST, limit=-1
    )


@api_view
async def program(request: HttpRequest, slug: str):
    state = request.services
    rows = await state.store.read(
        "programs", filters={**PUBLISHED_PORTFOLIO, "slug": {"_eq": slug}}, fields=DETAIL_FIELDS, limit=1
    )
    if not rows:
        raise ApiError(404, "Программа не найдена")
    program = rows[0]
    return {
        **program,
        "trainers": await published_trainers(state, program["id"]),
        "seats_left": await free_seats(state, program),
        "group": {"min": MIN_GROUP, "max": MAX_PARTICIPANTS},
    }
