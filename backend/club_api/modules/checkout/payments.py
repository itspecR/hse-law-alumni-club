import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit

from django.http import HttpRequest, JsonResponse

from club_api.core.errors import ApiError
from club_api.core.models import parse_date
from club_api.core.security import client_ip, trust_proxy, yookassa_ip
from club_api.core.views import api_view, json_body
from club_api.db.store import normalize
from club_api.domain import PORTFOLIO_CATALOG, rubles
from club_api.modules.auth.service import require_alumni
from club_api.modules.checkout.store import digest
from club_api.modules.notifications.mail import club_subject
from club_api.observability.audit import audit

logger = logging.getLogger("club.payments")
ROUTE_LIMITS = {"/orders/{number}/pay": 10, "/payments/yookassa/webhook": 60}
SAFE_INTEGER_MAX = 9007199254740991
PAYMENT_LOOKUP_PAGE_LIMIT = 20
PAYMENT_LOOKUP_PAGE_SIZE = 100


def secure_payment_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            return None
        _ = url.port
        return value
    except ValueError:
        return None


def payment_outcome(order, payment):
    if order["payment_id"] and order["payment_id"] != payment["id"]:
        return "ignored"
    if order["payment_status"] == "succeeded":
        return "duplicate"
    if payment["status"] == "succeeded":
        amount = payment.get("amount", {})
        value = amount.get("value")
        kop = (
            int(value.replace(".", ""))
            if isinstance(value, str) and len(value) <= 18 and re.fullmatch("\\d+\\.\\d{2}", value, re.ASCII)
            else None
        )
        if (
            amount.get("currency") != "RUB"
            or payment.get("paid") is not True
            or kop is None
            or (kop > SAFE_INTEGER_MAX)
            or (kop != order["total_estimate"])
            or (order["status"] in ("canceled", "expired"))
        ):
            return "review"
        return "succeeded"
    if (order["payment_status"] == "review" and order["payment_id"]) or order["status"] in ("canceled", "expired"):
        return "ignored"
    if payment["status"] == "canceled":
        return "review" if order["status"] == "done" else "canceled"
    if order["payment_status"] == "canceled":
        return "ignored"
    return "pending"


def next_podcast_expiry(current, months=12, now=None):
    now = now or datetime.now(UTC)
    previous = datetime.fromisoformat(current.replace("Z", "+00:00")) if isinstance(current, str) else current
    base = previous if previous and previous > now else now
    month = base.year * 12 + base.month - 1 + months
    return base.replace(year=month // 12, month=month % 12 + 1, day=1) + timedelta(days=base.day - 1)


class Payments:
    def __init__(self, state):
        self.state = state

    @property
    def enabled(self):
        settings = self.state.settings
        return bool(settings.YOOKASSA_SHOP_ID and settings.secret("YOOKASSA_SECRET_KEY"))

    async def provider(self, method, path, *, body=None, headers=None):
        settings = self.state.settings
        try:
            response = await self.state.client.request(
                method,
                "https://api.yookassa.ru/v3" + path,
                json=body,
                headers=headers,
                auth=(settings.YOOKASSA_SHOP_ID, settings.secret("YOOKASSA_SECRET_KEY")),
            )
            payment = response.json()
            if (
                not response.is_success
                or not isinstance(payment, dict)
                or (not isinstance(payment.get("id"), str))
                or (payment.get("status") not in ("pending", "waiting_for_capture", "succeeded", "canceled"))
            ):
                raise ValueError
            return payment
        except Exception as error:
            raise ApiError(502, "Не удалось получить ответ платежного сервиса.") from error

    async def fetch(self, id):
        payment = await self.provider("GET", "/payments/" + quote(id, safe=""))
        if payment["id"] != id:
            raise ApiError(502, "Не удалось проверить платеж.")
        return payment

    async def find_order_payment(self, order):
        settings = self.state.settings
        params = {
            "created_at.gte": parse_date(order["created_at"]).isoformat(),
            "created_at.lte": datetime.now(UTC).isoformat(),
            "limit": PAYMENT_LOOKUP_PAGE_SIZE,
        }
        matches, cursors = {}, set()
        try:
            for _ in range(PAYMENT_LOOKUP_PAGE_LIMIT):
                response = await self.state.client.get(
                    "https://api.yookassa.ru/v3/payments",
                    params=params,
                    auth=(settings.YOOKASSA_SHOP_ID, settings.secret("YOOKASSA_SECRET_KEY")),
                )
                page = response.json()
                if not response.is_success or page.get("type") != "list" or not isinstance(page.get("items"), list):
                    raise ValueError
                for payment in page["items"]:
                    if payment.get("metadata", {}).get("order_number") == order["number"]:
                        if not isinstance(payment.get("id"), str) or payment.get("status") not in (
                            "pending",
                            "waiting_for_capture",
                            "succeeded",
                            "canceled",
                        ):
                            raise ValueError
                        matches[payment["id"]] = payment
                cursor = page.get("next_cursor")
                if not cursor:
                    if len(matches) > 1:
                        raise ValueError
                    return next(iter(matches.values()), None)
                if not isinstance(cursor, str) or cursor in cursors:
                    raise ValueError
                cursors.add(cursor)
                params["cursor"] = cursor
            raise ValueError
        except Exception as error:
            raise ApiError(502, "Не удалось сверить платеж заявки.") from error

    async def flag_missing_payment(self, number):
        async with self.state.database.transaction() as connection:
            order = await (
                await connection.execute(
                    "SELECT id,status,payment_id,payment_status FROM orders WHERE number=%s FOR UPDATE", (number,)
                )
            ).fetchone()
            if (
                not order
                or order["status"] != "new"
                or order["payment_id"]
                or order["payment_status"] not in ("pending", "waiting_for_capture")
            ):
                return
            await connection.execute("UPDATE orders SET payment_status='review' WHERE id=%s", (order["id"],))
        await self.state.notifications.office_text(
            f"Заявка {number}: идентификатор платежа не сохранен. Проверьте платеж в ЮKassa, резерв сохранен до сверки."
        )

    async def create(self, number, amount, description, email=None):
        if type(amount) is not int or not 0 < amount <= SAFE_INTEGER_MAX:
            raise ApiError(400, "Некорректная сумма оплаты.")
        value = f"{amount // 100}.{amount % 100:02d}"
        body = {
            "amount": {"value": value, "currency": "RUB"},
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": f"{self.state.settings.PUBLIC_URL}/cart?paid={quote(number)}",
            },
            "description": description[:128],
            "metadata": {"order_number": number},
        }
        if email:
            body["receipt"] = {
                "customer": {"email": email},
                "items": [
                    {
                        "description": description[:128],
                        "quantity": "1.00",
                        "amount": {"value": value, "currency": "RUB"},
                        "vat_code": 1,
                        "payment_subject": "service",
                        "payment_mode": "full_payment",
                    }
                ],
            }
        return await self.provider(
            "POST", "/payments", body=body, headers={"Idempotence-Key": digest("order:" + number)[:36]}
        )

    async def extend(self, connection, alumni_id, months=12):
        cursor = await connection.execute("SELECT podcast_sub_until FROM alumni WHERE id=%s FOR UPDATE", (alumni_id,))
        profile = await cursor.fetchone()
        if not profile:
            raise ApiError(404, "Профиль подписчика не найден.")
        until = next_podcast_expiry(profile["podcast_sub_until"], months)
        await connection.execute(
            "UPDATE alumni SET podcast_sub_until=%s,podcast_reminder_sent=false WHERE id=%s", (until, alumni_id)
        )
        return normalize(until)

    async def extend_subscription(self, alumni_id, months=12):
        async with self.state.database.transaction() as connection:
            return await self.extend(connection, alumni_id, months)

    async def prepare(self, number, alumni_id):
        async with self.state.database.transaction() as connection:
            cursor = await connection.execute("SELECT * FROM orders WHERE number=%s FOR UPDATE", (number,))
            order = normalize(await cursor.fetchone())
            if not order:
                raise ApiError(404, "Заявка не найдена.")
            if order["alumni_id"] != alumni_id:
                raise ApiError(403, "Оплатить заявку может только тот, кто ее оформил.")
            if order["type"] == PORTFOLIO_CATALOG:
                raise ApiError(400, "Программу клуба оплачивают по договору.")
            if order["status"] in ("canceled", "expired"):
                raise ApiError(400, "Заявка закрыта.")
            if order["payment_status"] == "succeeded":
                raise ApiError(400, "Заявка уже оплачена.")
            if order["payment_status"] == "review":
                raise ApiError(409, "Платеж ожидает сверки, напишите в поддержку.")
            if type(order["total_estimate"]) is not int or not 0 < order["total_estimate"] <= SAFE_INTEGER_MAX:
                raise ApiError(400, "Некорректная сумма оплаты.")
            if not order["payment_id"]:
                await connection.execute("UPDATE orders SET payment_status='pending' WHERE id=%s", (order["id"],))
            return order

    async def creation_failed(self, number):
        async with self.state.database.transaction() as connection:
            cursor = await connection.execute(
                "SELECT id,payment_id,payment_status FROM orders WHERE number=%s FOR UPDATE", (number,)
            )
            order = await cursor.fetchone()
            if order and not order["payment_id"] and order["payment_status"] == "pending":
                await connection.execute("UPDATE orders SET payment_status=NULL WHERE id=%s", (order["id"],))

    async def apply(self, payment):
        number = payment.get("metadata", {}).get("order_number")
        if not number:
            return {"outcome": "missing"}
        async with self.state.database.transaction() as connection:
            cursor = await connection.execute("SELECT * FROM orders WHERE number=%s FOR UPDATE", (number,))
            order = normalize(await cursor.fetchone())
            if not order:
                return {"outcome": "missing"}
            outcome = payment_outcome(order, payment)
            if outcome == "succeeded":
                if order["type"] == "podcast" and order["alumni_id"]:
                    await self.extend(connection, order["alumni_id"])
                await connection.execute(
                    "UPDATE orders SET payment_id=%s,payment_status='succeeded',paid_at=now(),status=CASE WHEN status='new' THEN 'confirmed' ELSE status END WHERE id=%s",
                    (payment["id"], order["id"]),
                )
            elif outcome == "canceled":
                await self.state.checkout.release(connection, order["id"])
                await connection.execute(
                    "UPDATE orders SET payment_id=%s,payment_status='canceled',status='canceled' WHERE id=%s",
                    (payment["id"], order["id"]),
                )
            elif outcome in ("review", "pending"):
                await connection.execute(
                    "UPDATE orders SET payment_id=%s,payment_status=%s WHERE id=%s",
                    (payment["id"], payment["status"] if outcome == "pending" else outcome, order["id"]),
                )
            return {"outcome": outcome, "order": order}

    async def record(self, number, payment):
        return await self.apply({**payment, "metadata": {**payment.get("metadata", {}), "order_number": number}})


@api_view
async def payment_config(request: HttpRequest):
    return {"enabled": request.services.payments.enabled}


@api_view(permission=require_alumni)
async def pay(request: HttpRequest, number: str):
    payments = request.services.payments
    if not payments.enabled:
        raise ApiError(503, "Оплата на сайте пока не подключена.")
    alumni = await require_alumni(request)
    order = await payments.prepare(number, alumni["id"])
    if order["payment_id"]:
        existing = await payments.fetch(order["payment_id"])
        await payments.record(number, existing)
        if existing["status"] == "pending" and (
            url := secure_payment_url(existing.get("confirmation", {}).get("confirmation_url"))
        ):
            return {"payment_url": url}
        raise ApiError(
            409,
            "Предыдущий платеж отменен. Чтобы оплатить заново, обратитесь в поддержку."
            if existing["status"] == "canceled"
            else "Платеж уже обрабатывается. Проверьте статус заявки позже.",
        )
    try:
        payment = await payments.create(
            number,
            order["total_estimate"],
            f"Заявка {number} · Клуб выпускников факультета права Вышки",
            order["contact_email"],
        )
        await payments.record(number, payment)
    except ApiError:
        await payments.creation_failed(number)
        raise
    url = secure_payment_url(payment.get("confirmation", {}).get("confirmation_url"))
    if not url:
        raise ApiError(502, "Не удалось получить ссылку на оплату. Повторите позже.")
    return {"payment_url": url}


@api_view
async def webhook(request: HttpRequest):
    state = request.services
    if not state.payments.enabled:
        return JsonResponse({"ok": False}, status=503, safe=False)
    ip = client_ip(request).removeprefix("::ffff:")
    local = state.settings.APP_ENV != "production" and (ip in ("127.0.0.1", "::1") or trust_proxy(ip))
    if not local and (not yookassa_ip(ip)):
        await audit(request, "payment.webhook.badip", actor="ip:" + ip)
        return JsonResponse({"ok": False}, status=403, safe=False)
    try:
        body = json_body(request)
        if not isinstance(body.get("event"), str) or not isinstance(body.get("object", {}).get("id"), str):
            raise ValueError
    except ValueError, AttributeError, TypeError:
        return JsonResponse({"ok": False}, status=400, safe=False)
    try:
        payment = await state.payments.fetch(body["object"]["id"])
    except ApiError:
        return JsonResponse({"ok": False}, status=502, safe=False)
    result = await state.payments.apply(payment)
    order, outcome = (result.get("order"), result["outcome"])
    if order and outcome in ("review", "succeeded", "canceled"):
        await audit(
            request,
            "payment." + outcome,
            actor="yookassa",
            subject="order:" + order["number"],
            detail={"payment_id": payment["id"]},
        )
    if order and outcome == "succeeded" and order["contact_email"] and (order["contact_email"] != "-"):
        text = f"Здравствуйте, {order['contact_fio']}!\n\nОплата по заявке {order['number']} на сумму {rubles(order['total_estimate'])} прошла успешно."
        text += (
            "\nПодписка на подкаст клуба действует 12 месяцев."
            if order["type"] == "podcast"
            else "\nЗаявка передана в работу."
        )
        await state.notifications.mail_applicant(
            order["contact_email"], club_subject(f"Оплата по заявке {order['number']} получена"), text
        )
    return {"ok": True}
