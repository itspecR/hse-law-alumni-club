from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.views import api_view
from club_api.modules.campaigns.service import current_campaign, published_campaign

NOT_FOUND = "Сбор не найден."


@api_view
async def current(request: HttpRequest):
    return {"campaign": await current_campaign(request.services)}


@api_view
async def campaign(request: HttpRequest, slug: str):
    found = await published_campaign(request.services, slug)
    if not found:
        raise ApiError(404, NOT_FOUND)
    return found
