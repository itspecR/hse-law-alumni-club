import json
import logging
from functools import partial
from typing import Literal
from uuid import UUID, uuid4

from django.http import HttpRequest
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.domain import PORTFOLIO_CATALOG, effective_discount, order_totals, rubles
from club_api.modules.auth.routes import Body, EmailBody, RegisterBody
from club_api.modules.catalog.scope import PUBLISHED_PORTFOLIO
from club_api.modules.checkout.store import PENDING_NOTICE, digest
from club_api.modules.portfolio.captcha import CaptchaBody, require_captcha
from club_api.modules.portfolio.cohorts import canonical_id, chosen_cohort, cohort_price
from club_api.modules.portfolio.dates import display_date
from club_api.modules.portfolio.promo import valid_promo
from club_api.observability.audit import audit
from club_api.resources import shared_data

ROUTE_LIMITS = {
    ("POST", "/portfolio/{slug}/applications"): 6,
    ("POST", "/portfolio/{slug}/quote"): 30,
    ("GET", "/portfolio/challenge"): 30,
}
MAX_PARTICIPANTS = 20
MIN_GROUP = 2
PROMO_CODE_LENGTH = 32
PROMO_SOURCE = "promo"
MEMBER_SOURCE = "member"
CONSENTS = shared_data("legal-data.json")["portfolio_consents"]
logger = logging.getLogger("club.portfolio")


class QuoteBody(Body):
    applicant: Literal["self", "organization"]
    participants: int = Field(default=1, ge=1, le=MAX_PARTICIPANTS)
    promo_code: str | None = Field(default=None, max_length=PROMO_CODE_LENGTH)
    cohort_id: str | None = None

    @field_validator("promo_code")
    @classmethod
    def clean_code(cls, value):
        return promo_code(value)

    @field_validator("cohort_id")
    @classmethod
    def valid_cohort(cls, value):
        return canonical_id(value)


class ApplicationBody(QuoteBody):
    contact_fio: str = Field(min_length=2, max_length=200)
    contact_phone: str = Field(min_length=5, max_length=40)
    contact_email: str = Field(max_length=200)
    organization: str | None = Field(default=None, max_length=300)
    comment: str | None = Field(default=None, max_length=2000)
    consent_pdn: Literal[True]
    consent_terms: Literal[True]
    captcha: CaptchaBody | None = None
    website: str = Field(default="", max_length=0)

    @field_validator("contact_email")
    @classmethod
    def valid_email(cls, value):
        return EmailBody.email_valid(value)

    @field_validator("consent_pdn", "consent_terms", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        return RegisterBody.explicit_consent(value)


def promo_code(value):
    return (value or "").strip().upper() or None


def require_group(applicant, participants):
    if applicant == "organization" and participants < MIN_GROUP:
        raise ApiError(400, f"В заявке от организации — от {MIN_GROUP} до {MAX_PARTICIPANTS} участников")
    if applicant == "self" and participants != 1:
        raise ApiError(400, "Заявка для себя оформляется на одного участника")


def require_applicant(body):
    require_group(body.applicant, body.participants)
    organization = (body.organization or "").strip()
    if body.applicant == "organization" and not organization:
        raise ApiError(400, "Укажите организацию")
    if body.applicant == "self" and organization:
        raise ApiError(400, "Заявка для себя оформляется на одного участника")


def idempotency_key(request, slug):
    supplied = request.headers.get("idempotency-key")
    if supplied is not None:
        try:
            if len(supplied) != 36:
                raise ValueError
            UUID(supplied)
        except ValueError as error:
            raise ApiError(400, "Некорректный ключ заявки") from error
    return digest("portfolio:" + slug + ":" + (supplied or str(uuid4())))


async def open_program(state, slug):
    rows = await state.store.read(
        "programs",
        filters={**PUBLISHED_PORTFOLIO, "slug": {"_eq": slug}},
        fields=("id", "slug", "title", "price", "enrollment"),
        limit=1,
    )
    if not rows:
        raise ApiError(404, "Программа не найдена")
    if rows[0]["enrollment"] == "nonactual":
        raise ApiError(400, "Набор на эту программу закрыт")
    return rows[0]


def applicant_discount(applicant, alumni):
    if applicant != "self" or not alumni:
        return 0
    return effective_discount(
        alumni["verification_status"] == "verified", alumni["points_cached"] or 0, alumni["personal_discount"] or 0
    )


async def chosen_promo(connection, code, program, email, member, *, lock):
    if not code:
        return None, None
    try:
        promo = await valid_promo(connection, code, program["id"], email, lock=lock)
    except ApiError as error:
        if member:
            return None, error.message
        raise
    return (promo if promo["percent"] > member else None), None


def pricing(program, cohort, participants, member, promo):
    discount, source = (promo["percent"], PROMO_SOURCE) if promo else (member, MEMBER_SOURCE if member else None)
    items = [
        {
            "type": PORTFOLIO_CATALOG,
            "ref_id": program["slug"],
            "title": program["title"],
            "price": cohort_price(program, cohort),
            "qty": participants,
        }
    ]
    return items, order_totals(items, discount), discount, source


def promo_message(code, source, discount, rejected=None):
    if source == PROMO_SOURCE:
        return f"Промокод применён: скидка {discount}%"
    if rejected:
        return f"{rejected} — действует ваша скидка выпускника {discount}%"
    if code:
        return "Ваша скидка выпускника не меньше — промокод не понадобился"
    return None


def order_base(body, program, cohort, alumni, member, promo):
    items, totals, discount, source = pricing(program, cohort, body.participants, member, promo)
    return {
        "alumni_id": alumni["id"] if alumni else None,
        "type": PORTFOLIO_CATALOG,
        "program_id": program["id"],
        "cohort_id": cohort["id"] if cohort else None,
        "items_json": items,
        "subtotal": totals["subtotal"],
        "member_discount": discount,
        "total_estimate": totals["total"],
        "participants": body.participants,
        "organization": (body.organization or "").strip() or None,
        "contact_fio": body.contact_fio,
        "contact_phone": body.contact_phone,
        "contact_email": body.contact_email,
        "comment": body.comment,
        "consent_pdn": True,
        "consents": CONSENTS,
        "promo_id": promo["id"] if promo else None,
        "promo_code": promo["code"] if promo else None,
        "discount_source": source,
        "status": "new",
        "payment_status": None,
    }


def cohort_start(choice):
    return choice["cohort"]["starts_on"] if choice.get("cohort") else None


def application_receipt(code, choice, number, base, notified=PENDING_NOTICE):
    return {
        "number": number,
        "status": base["status"],
        "participants": base["participants"],
        "member_discount": base["member_discount"],
        "discount_source": base["discount_source"],
        "promo_code": base["promo_code"],
        "message": promo_message(code, base["discount_source"], base["member_discount"], choice.get("rejected")),
        "starts_on": cohort_start(choice),
        "subtotal": base["subtotal"],
        "total_estimate": base["total_estimate"],
        "notified": notified,
    }


def summary(base, starts_on=None):
    text = base["items_json"][0]["title"] + (f" · поток с {display_date(starts_on)}" if starts_on else "")
    text += f" · участников: {base['participants']}"
    return text + (f" · {base['organization']}" if base["organization"] else "")


async def notify(state, number, base, starts_on):
    text = summary(base, starts_on)
    notified = await state.notifications.order_notice(number, text, base["total_estimate"], base["member_discount"])
    await state.notifications.mail_applicant(
        base["contact_email"],
        f"Заявка {number} принята – Клуб выпускников факультета права",
        f"Здравствуйте, {base['contact_fio']}!\n\nВаша заявка {number} принята:\n{text}\n"
        f"Сумма (справочно): {rubles(base['total_estimate'])}.\n\n"
        "Оплата на сайте не требуется: менеджер учебного офиса свяжется с вами, чтобы согласовать договор и оплату.",
    )
    return notified


@api_view(body=ApplicationBody)
async def apply(request: HttpRequest, slug: str):
    body = parse_body(request, ApplicationBody)
    require_applicant(body)
    state = request.services
    alumni = await state.auth.resolve_alumni(request)
    key = idempotency_key(request, slug)
    request_hash = digest(
        json.dumps(
            {"slug": slug, "body": body.model_dump(exclude={"captcha"}), "alumni": alumni["id"] if alumni else None},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if previous := await state.checkout.replay(key, request_hash):
        return previous
    await require_captcha(state, body.captcha)
    program = await open_program(state, slug)
    member = applicant_discount(body.applicant, alumni)

    choice = {}

    async def build(connection):
        choice["cohort"] = await chosen_cohort(state, program["id"], body.cohort_id, connection, lock=True)
        choice["promo"], choice["rejected"] = await chosen_promo(
            connection, body.promo_code, program, body.contact_email, member, lock=True
        )
        return order_base(body, program, choice["cohort"], alumni, member, choice["promo"])

    receipt = partial(application_receipt, body.promo_code, choice)
    result = await state.checkout.commit_single(key=key, request_hash=request_hash, build=build, receipt=receipt)
    if result["replay"]:
        return result["replay"]
    number, base = result["number"], result["base"]
    receipt = receipt(number, base, await notify(state, number, base, cohort_start(choice)))
    await audit(
        request,
        "order.created",
        actor="alumni:" + alumni["id"] if alumni else "guest",
        subject="order:" + number,
        detail={
            "total": base["total_estimate"],
            "discount": base["member_discount"],
            "type": PORTFOLIO_CATALOG,
            "promo": base["promo_code"],
            "cohort": base["cohort_id"],
        },
    )
    try:
        await state.checkout.save_receipt(key, receipt)
    except Exception:
        logger.error("Не удалось обновить квитанцию после сохранения заявки")
    return receipt


@api_view(body=QuoteBody)
async def quote(request: HttpRequest, slug: str):
    body = parse_body(request, QuoteBody)
    require_group(body.applicant, body.participants)
    state = request.services
    alumni = await state.auth.resolve_alumni(request)
    program = await open_program(state, slug)
    member = applicant_discount(body.applicant, alumni)
    async with state.database.connection() as connection:
        cohort = await chosen_cohort(state, program["id"], body.cohort_id, connection)
        promo, rejected = await chosen_promo(connection, body.promo_code, program, None, member, lock=False)
    _, totals, discount, source = pricing(program, cohort, body.participants, member, promo)
    return {
        "subtotal": totals["subtotal"],
        "discount": discount,
        "source": source,
        "total": totals["total"],
        "message": promo_message(body.promo_code, source, discount, rejected),
    }
