from typing import Literal

from django.http import HttpRequest
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.names import OptionalPersonName
from club_api.core.views import api_view, parse_body
from club_api.modules.auth.routes import EmailBody, RegisterBody
from club_api.modules.campaigns.store import published_by_slug
from club_api.modules.checkout.payments import secure_payment_url
from club_api.modules.donations.service import CARD, TRANSFER
from club_api.modules.portfolio.captcha import CaptchaBody, require_captcha
from club_api.observability.audit import audit

ROUTE_LIMITS = {"/donations": 10}
DONATION_MIN_RUB = 100
DONATION_MAX_RUB = 500000
KOPECKS = 100
DISABLED = "Прием пожертвований на сайте пока не подключен."
METHOD_DISABLED = "Этот способ оплаты пока не подключен."
CAMPAIGN_SLUG_MAX = 64
CAMPAIGN_NOT_FOUND = "Сбор не найден или уже закрыт."
GIFT_REQUIRED = "Подтвердите, что пожертвование безвозмездно."


class DonationBody(EmailBody):
    amount: int = Field(ge=DONATION_MIN_RUB, le=DONATION_MAX_RUB)
    method: Literal[CARD, TRANSFER] = CARD
    name: OptionalPersonName = ""
    consent_offer: Literal[True]
    consent_pdn: Literal[True]
    campaign: str | None = Field(default=None, min_length=1, max_length=CAMPAIGN_SLUG_MAX)
    consent_gift: bool = False
    captcha: CaptchaBody | None = None
    website: str = Field(default="", max_length=0)

    @field_validator("consent_offer", "consent_pdn", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        return RegisterBody.explicit_consent(value)

    @field_validator("consent_gift", mode="before")
    @classmethod
    def explicit_gift(cls, value):
        return value is True


def methods(state):
    return {CARD: state.payments.enabled, TRANSFER: state.transfer.enabled}


@api_view
async def donation_config(request: HttpRequest):
    available = methods(request.services)
    return {"enabled": any(available.values()), **available, "min": DONATION_MIN_RUB, "max": DONATION_MAX_RUB}


async def card_reply(state, donation):
    try:
        payment = await state.donations.start_payment(donation)
    except ApiError:
        await state.donations.mark_failed(donation["number"])
        raise
    await state.donations.apply(payment)
    url = secure_payment_url(payment.get("confirmation", {}).get("confirmation_url"))
    if not url:
        raise ApiError(502, "Не удалось получить ссылку на оплату. Повторите позже.")
    return {"payment_url": url}


async def transfer_reply(state, donation):
    return {"transfer": await state.donations.start_transfer(donation)}


async def campaign_id(state, slug):
    if not slug:
        return None
    campaign = await published_by_slug(state, slug, ("id",))
    if not campaign:
        raise ApiError(404, CAMPAIGN_NOT_FOUND)
    return campaign["id"]


@api_view(body=DonationBody)
async def donate(request: HttpRequest):
    state = request.services
    available = methods(state)
    if not any(available.values()):
        raise ApiError(503, DISABLED)
    body = parse_body(request, DonationBody)
    if not available[body.method]:
        raise ApiError(503, METHOD_DISABLED)
    if body.campaign and not body.consent_gift:
        raise ApiError(400, GIFT_REQUIRED)
    await require_captcha(state, body.captcha)
    campaign = await campaign_id(state, body.campaign)
    alumni = await state.auth.resolve_alumni(request)
    donation = await state.donations.create(
        amount=body.amount * KOPECKS,
        email=body.email.strip().lower(),
        name=body.name.strip(),
        alumni_id=alumni["id"] if alumni else None,
        method=body.method,
        campaign_id=campaign,
    )
    reply = await (transfer_reply if body.method == TRANSFER else card_reply)(state, donation)
    await audit(
        request,
        "donation.created",
        actor="alumni:" + alumni["id"] if alumni else "guest",
        subject="donation:" + donation["number"],
        detail={"amount": donation["amount"], "method": body.method, "campaign": body.campaign},
    )
    return {"number": donation["number"], **reply}
