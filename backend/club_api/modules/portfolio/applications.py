import json
from typing import Literal
from uuid import UUID, uuid4

from django.http import HttpRequest
from pydantic import Field, field_validator

from club_api.core.errors import ApiError
from club_api.core.views import api_view, parse_body
from club_api.domain import PORTFOLIO_CATALOG, effective_discount, order_totals
from club_api.modules.auth.routes import Body, EmailBody, RegisterBody
from club_api.modules.catalog.scope import PUBLISHED_PORTFOLIO
from club_api.modules.checkout.store import digest
from club_api.observability.audit import audit
from club_api.resources import shared_data

ROUTE_LIMITS = {("POST", "/portfolio/{slug}/applications"): 6}
MAX_PARTICIPANTS = 20
MIN_GROUP = 2
CONSENTS = shared_data("legal-data.json")["portfolio_consents"]


class ApplicationBody(Body):
    contact_fio: str = Field(min_length=2, max_length=200)
    contact_phone: str = Field(min_length=5, max_length=40)
    contact_email: str = Field(max_length=200)
    applicant: Literal["self", "organization"]
    organization: str | None = Field(default=None, max_length=300)
    participants: int = Field(default=1, ge=1, le=MAX_PARTICIPANTS)
    comment: str | None = Field(default=None, max_length=2000)
    consent_pdn: Literal[True]
    consent_terms: Literal[True]
    website: str = Field(default="", max_length=0)

    @field_validator("contact_email")
    @classmethod
    def valid_email(cls, value):
        return EmailBody.email_valid(value)

    @field_validator("consent_pdn", "consent_terms", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        return RegisterBody.explicit_consent(value)


def require_applicant(body):
    if body.applicant == "organization":
        if not (body.organization or "").strip():
            raise ApiError(400, "Укажите организацию")
        if body.participants < MIN_GROUP:
            raise ApiError(400, f"В заявке от организации — от {MIN_GROUP} до {MAX_PARTICIPANTS} участников")
    elif body.participants != 1 or (body.organization or "").strip():
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


def applicant_discount(body, alumni):
    if body.applicant != "self" or not alumni:
        return 0
    return effective_discount(
        alumni["verification_status"] == "verified", alumni["points_cached"] or 0, alumni["personal_discount"] or 0
    )


def order_base(body, program, alumni, discount):
    items = [
        {
            "type": PORTFOLIO_CATALOG,
            "ref_id": program["slug"],
            "title": program["title"],
            "price": program["price"] or 0,
            "qty": body.participants,
        }
    ]
    totals = order_totals(items, discount)
    return {
        "alumni_id": alumni["id"] if alumni else None,
        "type": PORTFOLIO_CATALOG,
        "program_id": program["id"],
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
        "status": "new",
        "payment_status": None,
    }


def summary(base):
    text = f"{base['items_json'][0]['title']} · участников: {base['participants']}"
    return text + (f" · {base['organization']}" if base["organization"] else "")


async def notify(state, number, base):
    notified = await state.notifications.order_notice(
        number, summary(base), base["total_estimate"], base["member_discount"]
    )
    await state.notifications.send_email(
        base["contact_email"],
        f"Заявка {number} принята – Клуб выпускников факультета права",
        f"Здравствуйте, {base['contact_fio']}!\n\nВаша заявка {number} принята:\n{summary(base)}\n"
        f"Сумма (справочно): {base['total_estimate'] / 100:g} ₽.\n\n"
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
            {"slug": slug, "body": body.model_dump(), "alumni": alumni["id"] if alumni else None},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if previous := await state.checkout.replay(key, request_hash):
        return previous
    program = await open_program(state, slug)
    base = order_base(body, program, alumni, applicant_discount(body, alumni))
    result = await state.checkout.commit_single(key=key, request_hash=request_hash, base=base)
    if result["replay"]:
        return result["replay"]
    number = result["number"]
    receipt = {
        "number": number,
        "status": "new",
        "participants": base["participants"],
        "member_discount": base["member_discount"],
        "subtotal": base["subtotal"],
        "total_estimate": base["total_estimate"],
        "notified": await notify(state, number, base),
    }
    await audit(
        request,
        "order.created",
        actor="alumni:" + alumni["id"] if alumni else "guest",
        subject="order:" + number,
        detail={"total": base["total_estimate"], "discount": base["member_discount"], "type": PORTFOLIO_CATALOG},
    )
    await state.checkout.save_receipt(key, receipt)
    return receipt
