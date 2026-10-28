from typing import Annotated

from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.security import constant_equal
from club_api.core.views import api_view, defer, parse_body
from club_api.modules.auth.routes import Body

ROUTE_LIMITS = {("POST", "/telegram/webhook"): 120}
SafeId = Annotated[int, Field(ge=-9007199254740991, le=9007199254740991)]


class Chat(Body):
    id: SafeId
    type: str = None


class Sender(Body):
    id: SafeId
    is_bot: bool = None
    first_name: str = None


class Message(Body):
    message_id: SafeId
    text: str = Field(default=None, max_length=4096)
    chat: Chat
    sender: Sender = Field(default=None, alias="from")


class Reaction(Body):
    chat: Chat
    message_id: SafeId
    user: Sender = None
    date: int
    new_reaction: list = Field(max_length=100)


class UpdateBody(Body):
    update_id: SafeId
    message: Message = None
    message_reaction: Reaction = None


@api_view(body=UpdateBody)
async def webhook(request: HttpRequest):
    body = parse_body(request, UpdateBody)
    state = request.services
    if not state.settings.secret("TELEGRAM_BOT_TOKEN"):
        raise ApiError(503, "Telegram-бот не настроен.")
    if not state.settings.secret("TELEGRAM_WEBHOOK_SECRET"):
        raise ApiError(503, "Webhook не настроен.")
    if not constant_equal(
        request.headers.get("x-telegram-bot-api-secret-token"), state.settings.secret("TELEGRAM_WEBHOOK_SECRET")
    ):
        raise ApiError(403, "Нет доступа.")
    update = body.model_dump(by_alias=True, exclude_none=True)
    if body.message_reaction:
        await state.telegram.handle(update)
    else:
        defer(request, state.telegram.safe_handle, update)
    return {"ok": True}
