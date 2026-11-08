import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.utils import timezone

from club_api.core.errors import ApiError
from club_api.modules.fiscal.queue import FAILED, MAX_ATTEMPTS, REGISTERED, SUCCESS, receipts

BACKEND = Path(__file__).resolve().parents[2]
RECEIPT = {"type": "SALE", "total": 100.0}


class Cashbox:
    enabled = True

    def __init__(self):
        self.registered = []
        self.statuses = ["REGISTRATION", "SUCCESS"]
        self.failure = None

    async def register(self, receipt, key):
        if self.failure:
            raise ApiError(502, self.failure)
        self.registered.append((receipt, key))
        return "628726"

    async def document(self, id):
        assert id == "628726"
        status = self.statuses.pop(0)
        return {"status": status, "fiscalDocument": 119, "fiscalSign": "509420994", "fiscalDrive": "9999078902013448"}


async def order(app, number="ALU-2026-000001"):
    return await app.state.store.create("orders", {"number": number, "type": "merch", "status": "new"})


async def row(app, key):
    return await app.state.database.orm(lambda: receipts().get(idempotency_key=key))


async def make_due(app, key):
    await app.state.database.orm(
        lambda: receipts().filter(idempotency_key=key).update(next_attempt_at=timezone.now() - timedelta(seconds=1))
    )


def prepared(app, monkeypatch):
    cashbox, notices = Cashbox(), []
    app.state.bifit = cashbox

    async def office_text(text):
        notices.append(text)

    monkeypatch.setattr(app.state.notifications, "office_text", office_text)
    return cashbox, notices


async def test_receipt_is_queued_once_per_order(database_app):
    app = database_app
    created = await order(app)
    first = await app.state.fiscal.enqueue(created["id"], RECEIPT)
    assert await app.state.fiscal.enqueue(created["id"], {"type": "SALE", "total": 1.0}) == first
    assert (await row(app, first)).request == RECEIPT


async def test_queued_receipt_is_registered_polled_and_stored_with_fiscal_data(database_app, monkeypatch):
    app = database_app
    cashbox, notices = prepared(app, monkeypatch)
    key = await app.state.fiscal.enqueue((await order(app))["id"], RECEIPT)
    assert (await app.state.fiscal.drain())["registered"] == 1
    assert cashbox.registered == [(RECEIPT, key)]
    stored = await row(app, key)
    assert (stored.status, stored.document_id) == (REGISTERED, "628726")
    assert (await app.state.fiscal.drain())["registered"] == 0
    await make_due(app, key)
    assert (await app.state.fiscal.drain())["waiting"] == 1
    await make_due(app, key)
    assert (await app.state.fiscal.drain())["succeeded"] == 1
    stored = await row(app, key)
    assert stored.status == SUCCESS and stored.fiscal["fiscalDocument"] == 119 and notices == []
    assert len(cashbox.registered) == 1


async def test_rejected_receipt_is_failed_and_office_is_notified(database_app, monkeypatch):
    app = database_app
    cashbox, notices = prepared(app, monkeypatch)
    cashbox.statuses = ["FAIL"]
    key = await app.state.fiscal.enqueue((await order(app, "ALU-2026-000002"))["id"], RECEIPT)
    await app.state.fiscal.drain()
    await make_due(app, key)
    assert (await app.state.fiscal.drain())["failed"] == 1
    assert (await row(app, key)).status == FAILED
    assert len(notices) == 1 and "ALU-2026-000002" in notices[0]


async def test_unreachable_cashbox_is_retried_then_failed_with_notice(database_app, monkeypatch):
    app = database_app
    cashbox, notices = prepared(app, monkeypatch)
    cashbox.failure = "Не удалось получить ответ онлайн-кассы."
    key = await app.state.fiscal.enqueue((await order(app, "ALU-2026-000003"))["id"], RECEIPT)
    await app.state.fiscal.drain()
    stored = await row(app, key)
    assert stored.attempts == 1 and stored.last_error == cashbox.failure and stored.next_attempt_at > timezone.now()
    for _ in range(MAX_ATTEMPTS - 1):
        await make_due(app, key)
        await app.state.fiscal.drain()
    stored = await row(app, key)
    assert stored.status == FAILED and stored.attempts == MAX_ATTEMPTS
    assert len(notices) == 1 and "ALU-2026-000003" in notices[0]


async def test_disabled_cashbox_leaves_queue_untouched(database_app, monkeypatch):
    app = database_app
    cashbox, _ = prepared(app, monkeypatch)
    cashbox.enabled = False
    key = await app.state.fiscal.enqueue((await order(app))["id"], RECEIPT)
    assert sum((await app.state.fiscal.drain()).values()) == 0
    assert (await row(app, key)).status == "pending" and cashbox.registered == []


def test_api_module_imports_before_django_setup():
    environment = {name: value for name, value in os.environ.items() if name != "DJANGO_SETTINGS_MODULE"}
    result = subprocess.run(
        [sys.executable, "-c", "import club_api.main"], cwd=BACKEND, env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-500:]
