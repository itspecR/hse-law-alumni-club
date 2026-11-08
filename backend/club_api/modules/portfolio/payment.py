import hashlib
import re
import secrets

from django.http import HttpRequest

from club_api.core.errors import ApiError
from club_api.core.models import guid
from club_api.core.views import api_view
from club_api.db.store import normalize
from club_api.domain import PORTFOLIO_CATALOG
from club_api.modules.auth.service import require_admin, require_full_admin
from club_api.modules.checkout.payments import mail_paid
from club_api.modules.notifications.mail import CLUB_NAME, club_subject
from club_api.modules.transfer.qr import PURPOSE_MAX, UNAVAILABLE, requisites_text
from club_api.observability.audit import audit

ROUTE_LIMITS = {("GET", "/transfers/{token}"): 30}
AWAITING = "awaiting"
SUCCEEDED = "succeeded"
OPEN_STATUSES = frozenset(("new", "in_progress", "confirmed"))
SENDABLE_PAYMENTS = frozenset((None, "", AWAITING))
PAY_PATH = "/pay/"
TOKEN_BYTES = 32
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
PURPOSE_START = "Оплата участия в программе «"
ELLIPSIS = "…"
LOCKED_SQL = "SELECT * FROM orders WHERE id=%s FOR UPDATE"
LINK_SQL = "SELECT * FROM orders WHERE transfer_token=%s"
NOT_FOUND = "Заявка не найдена."
LINK_NOT_FOUND = "Ссылка на оплату не найдена или устарела."
NOT_PROGRAM = "Реквизиты отправляются только по заявкам на программы клуба."
CLOSED = "Заявка закрыта."
PAID = "Заявка уже оплачена."
OTHER_PAYMENT = "По заявке уже идет оплата другим способом."
NO_PRICE = "Сначала укажите стоимость заявки."
NO_EMAIL = "У заявки нет почты для отправки реквизитов."
NOT_AWAITING = "Заявка не ждет оплаты переводом."


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def program_title(order):
    items = order["items_json"] or []
    return items[0]["title"] if items else ""


def payment_purpose(title, number):
    tail = f"», заявка {number}"
    room = PURPOSE_MAX - len(PURPOSE_START) - len(tail)
    if len(title) > room:
        title = title[: room - len(ELLIPSIS)] + ELLIPSIS
    return PURPOSE_START + title + tail


def sendable(order):
    if not order:
        raise ApiError(404, NOT_FOUND)
    if order["type"] != PORTFOLIO_CATALOG:
        raise ApiError(409, NOT_PROGRAM)
    if order["status"] not in OPEN_STATUSES:
        raise ApiError(409, CLOSED)
    if order["payment_status"] == SUCCEEDED:
        raise ApiError(409, PAID)
    if order["payment_status"] not in SENDABLE_PAYMENTS:
        raise ApiError(409, OTHER_PAYMENT)
    if type(order["total_estimate"]) is not int or order["total_estimate"] <= 0:
        raise ApiError(409, NO_PRICE)
    if not order["contact_email"] or order["contact_email"] == "-":
        raise ApiError(409, NO_EMAIL)
    return order


def requisites_letter(order, transfer, link):
    return (
        f"Здравствуйте, {order['contact_fio']}!\n\n"
        f"Реквизиты для оплаты по заявке {order['number']} на программу «{program_title(order)}». "
        f"Оплатить можно по QR-коду на странице {link} или переводом по реквизитам.\n\n"
        f"{requisites_text(transfer)}\n\n"
        f"Когда оплата поступит на счет клуба, мы пришлем письмо.\n\n{CLUB_NAME}"
    )


class ProgramPayments:
    def __init__(self, state):
        self.state = state

    def details(self, order):
        return self.state.transfer.details(
            order["total_estimate"], payment_purpose(program_title(order), order["number"])
        )

    async def send(self, id):
        if not self.state.transfer.enabled:
            raise ApiError(503, UNAVAILABLE)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        async with self.state.database.transaction() as connection:
            order = sendable(normalize(await (await connection.execute(LOCKED_SQL, (id,))).fetchone()))
            transfer = self.details(order)
            await connection.execute(
                "UPDATE orders SET payment_status=%s,transfer_token=%s,transfer_sent_at=now() WHERE id=%s",
                (AWAITING, token_hash(token), order["id"]),
            )
        link = self.state.settings.PUBLIC_URL + PAY_PATH + token
        await self.state.notifications.mail_applicant(
            order["contact_email"],
            club_subject(f"Реквизиты для оплаты по заявке {order['number']}"),
            requisites_letter(order, transfer, link),
        )
        return order

    async def page(self, token):
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise ApiError(404, LINK_NOT_FOUND)
        rows = await self.state.database.rows(LINK_SQL, (token_hash(token),))
        if not rows:
            raise ApiError(404, LINK_NOT_FOUND)
        order = normalize(rows[0])
        reply = {"number": order["number"], "program": program_title(order)}
        if order["payment_status"] == SUCCEEDED:
            return {**reply, "paid": True}
        if order["status"] not in OPEN_STATUSES or order["payment_status"] != AWAITING:
            raise ApiError(410, CLOSED)
        return {**reply, "paid": False, "transfer": self.details(order)}

    async def receive(self, id):
        async with self.state.database.transaction() as connection:
            order = normalize(await (await connection.execute(LOCKED_SQL, (id,))).fetchone())
            if not order:
                raise ApiError(404, NOT_FOUND)
            if order["payment_status"] != AWAITING:
                raise ApiError(409, NOT_AWAITING)
            await connection.execute(
                "UPDATE orders SET payment_status=%s,paid_at=now() WHERE id=%s", (SUCCEEDED, order["id"])
            )
        await mail_paid(self.state, order)
        return order


@api_view(permission=require_admin)
async def send_requisites(request: HttpRequest, id: str):
    admin = await require_admin(request)
    order = await request.services.program_payments.send(guid(id))
    await audit(
        request,
        "order.transfer_sent",
        actor="admin:" + admin["userId"],
        subject="order:" + order["number"],
        detail={"amount": order["total_estimate"]},
    )
    return {"ok": True}


@api_view(permission=require_full_admin)
async def receive_payment(request: HttpRequest, id: str):
    admin = await require_full_admin(request)
    order = await request.services.program_payments.receive(guid(id))
    await audit(
        request,
        "order.transfer_received",
        actor="admin:" + admin["userId"],
        subject="order:" + order["number"],
        detail={"amount": order["total_estimate"]},
    )
    return {"ok": True}


@api_view
async def payment_page(request: HttpRequest, token: str):
    return await request.services.program_payments.page(token)
