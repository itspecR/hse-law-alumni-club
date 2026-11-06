from typing import Literal

from django.http import HttpRequest
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.modules.auth.routes import EmailBody, RegisterBody
from club_api.modules.checkout.payments import secure_payment_url
from club_api.modules.portfolio.captcha import CaptchaBody, require_captcha
from club_api.observability.audit import audit

ROUTE_LIMITS = {"/donations": 10}
DONATION_MIN_RUB = 100
DONATION_MAX_RUB = 500000
KOPECKS = 100
DISABLED = "Прием пожертвований на сайте пока не подключен."


class DonationBody(EmailBody):
    amount: int = Field(ge=DONATION_MIN_RUB, le=DONATION_MAX_RUB)
    name: str = Field(default="", max_length=200)
    consent_offer: Literal[True]
    consent_pdn: Literal[True]
    captcha: CaptchaBody | None = None
    website: str = Field(default="", max_length=0)

    @field_validator("consent_offer", "consent_pdn", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        return RegisterBody.explicit_consent(value)


@api_view
async def donation_config(request: HttpRequest):
    return {"enabled": request.services.payments.enabled, "min": DONATION_MIN_RUB, "max": DONATION_MAX_RUB}


@api_view(body=DonationBody)
async def donate(request: HttpRequest):
    state = request.services
    if not state.payments.enabled:
        raise ApiError(503, DISABLED)
    body = parse_body(request, DonationBody)
    await require_captcha(state, body.captcha)
    alumni = await state.auth.resolve_alumni(request)
    donation = await state.donations.create(
        amount=body.amount * KOPECKS,
        email=body.email.strip().lower(),
        name=body.name.strip(),
        alumni_id=alumni["id"] if alumni else None,
    )
    try:
        payment = await state.donations.start_payment(donation)
    except ApiError:
        await state.donations.mark_failed(donation["number"])
        raise
    await state.donations.apply(payment)
    url = secure_payment_url(payment.get("confirmation", {}).get("confirmation_url"))
    if not url:
        raise ApiError(502, "Не удалось получить ссылку на оплату. Повторите позже.")
    await audit(
        request,
        "donation.created",
        actor="alumni:" + alumni["id"] if alumni else "guest",
        subject="donation:" + donation["number"],
        detail={"amount": donation["amount"]},
    )
    return {"number": donation["number"], "payment_url": url}
