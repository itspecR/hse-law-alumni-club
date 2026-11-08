import uuid
from datetime import timedelta

from django.utils import timezone

from club_api.core.errors import ApiError

SALE = "sale"
PENDING = "pending"
REGISTERED = "registered"
SUCCESS = "success"
FAILED = "failed"
POLL_SECONDS = 60
RETRY_SECONDS = (60, 300, 900, 3600)
MAX_ATTEMPTS = 8
DRAIN_LIMIT = 20
FISCAL_FIELDS = ("fiscalDocument", "fiscalSign", "fiscalDrive", "fiscalTime", "fiscalAmount")
REJECTED = "Касса отклонила чек"


def receipts():
    from club_api.db.models import FiscalReceipts

    return FiscalReceipts.objects


def retry_delay(attempts):
    return timedelta(seconds=RETRY_SECONDS[min(attempts, len(RETRY_SECONDS)) - 1])


def failure_notice(number, reason):
    return f"Чек по заявке {number or 'без номера'} не прошел в онлайн-кассе: {reason}. Проверьте документ в кабинете БИФИТ."


class FiscalQueue:
    def __init__(self, state):
        self.state = state

    async def enqueue(self, order_id, receipt, kind=SALE):
        async with self.state.database.transaction() as connection:
            await connection.lock(f"fiscal:{order_id}:{kind}")

            def create():
                existing = receipts().filter(order_id=order_id, kind=kind).first()
                if existing:
                    return existing.idempotency_key
                key = str(uuid.uuid4())
                receipts().create(order_id=order_id, kind=kind, idempotency_key=key, request=receipt)
                return key

            return await connection.run(create)

    async def due(self, limit):
        return await self.state.database.orm(
            lambda: list(
                receipts()
                .filter(status__in=(PENDING, REGISTERED), next_attempt_at__lte=timezone.now())
                .order_by("id")
                .values_list("id", flat=True)[:limit]
            )
        )

    async def load(self, id):
        return await self.state.database.orm(lambda: receipts().select_related("order").filter(pk=id).first())

    async def save(self, id, **fields):
        await self.state.database.orm(lambda: receipts().filter(pk=id).update(updated_at=timezone.now(), **fields))

    async def drain(self, limit=DRAIN_LIMIT):
        result = {"registered": 0, "succeeded": 0, "failed": 0, "waiting": 0}
        if not self.state.database.pool or not self.state.bifit.enabled:
            return result
        for id in await self.due(limit):
            receipt = await self.load(id)
            if receipt and receipt.status in (PENDING, REGISTERED):
                result[await self.advance(receipt)] += 1
        return result

    async def advance(self, receipt):
        try:
            if receipt.status == PENDING:
                return await self.register(receipt)
            answer = await self.state.bifit.document(receipt.document_id)
        except ApiError as error:
            return await self.retry(receipt, error.message)
        if answer["status"] == "SUCCESS":
            fiscal = {name: answer.get(name) for name in FISCAL_FIELDS}
            await self.save(receipt.pk, status=SUCCESS, fiscal=fiscal, last_error=None)
            return "succeeded"
        if answer["status"] == "FAIL":
            return await self.fail(receipt, REJECTED)
        await self.save(receipt.pk, next_attempt_at=timezone.now() + timedelta(seconds=POLL_SECONDS))
        return "waiting"

    async def register(self, receipt):
        document = await self.state.bifit.register(receipt.request, receipt.idempotency_key)
        await self.save(
            receipt.pk,
            status=REGISTERED,
            document_id=document,
            last_error=None,
            next_attempt_at=timezone.now() + timedelta(seconds=POLL_SECONDS),
        )
        return "registered"

    async def retry(self, receipt, reason):
        attempts = receipt.attempts + 1
        if attempts >= MAX_ATTEMPTS:
            await self.save(receipt.pk, attempts=attempts)
            return await self.fail(receipt, reason)
        await self.save(
            receipt.pk, attempts=attempts, last_error=reason, next_attempt_at=timezone.now() + retry_delay(attempts)
        )
        return "waiting"

    async def fail(self, receipt, reason):
        await self.save(receipt.pk, status=FAILED, last_error=reason)
        await self.state.notifications.office_text(failure_notice(receipt.order and receipt.order.number, reason))
        return "failed"
