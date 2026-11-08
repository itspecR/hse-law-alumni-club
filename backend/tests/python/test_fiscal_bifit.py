from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import ValidationError

from club_api.core.config import Settings
from club_api.core.errors import ApiError
from club_api.modules.fiscal.bifit import CONNECTOR_CLIENT, TOKEN_RENEWAL_MARGIN_SECONDS, Bifit

TEST_URL = "https://fp-test.bifit.com/processing-api"
LIVE_URL = "https://fp.bifit.com/processing-api"
RECEIPT = {"type": "SALE", "total": 150.0}


def settings(**values):
    return Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only", **values)


def configured(**values):
    return settings(BIFIT_API_URL=TEST_URL, BIFIT_CONNECTOR_TOKEN="synthetic-connector-token", **values)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Processing:
    def __init__(self):
        self.calls = []
        self.grants = 0
        self.unauthorized = 0
        self.refresh_fails = False
        self.document = 254934
        self.status = {"status": "SUCCESS", "fiscalDocument": 5088}

    def token(self, form):
        if form["grant_type"] == "refresh_token" and self.refresh_fails:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.grants += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{self.grants}",
                "refresh_token": f"refresh-{self.grants}",
                "expires_in": 3599,
                "token_type": "bearer",
            },
        )

    def __call__(self, request):
        self.calls.append(request)
        if request.url.path.endswith("/oauth/token"):
            return self.token({key: value[0] for key, value in parse_qs(request.content.decode()).items()})
        if self.unauthorized:
            self.unauthorized -= 1
            return httpx.Response(401, json={"error": "invalid_token"})
        if request.method == "POST":
            return httpx.Response(200, json=self.document)
        return httpx.Response(200, json=self.status)

    def forms(self):
        return [
            {key: value[0] for key, value in parse_qs(call.content.decode()).items()}
            for call in self.calls
            if call.url.path.endswith("/oauth/token")
        ]


def bifit(processing, clock=None, **values):
    client = httpx.AsyncClient(transport=httpx.MockTransport(processing))
    return Bifit(configured(**values), client, clock or Clock())


async def test_disabled_without_address_and_token():
    with pytest.raises(ApiError) as error:
        await Bifit(settings(), httpx.AsyncClient(), Clock()).register(RECEIPT, "key-1")
    assert error.value.status == 503


async def test_receipt_is_sent_with_connector_token_bearer_and_idempotency_key():
    processing = Processing()
    assert await bifit(processing).register(RECEIPT, "order-1") == "254934"
    assert processing.forms() == [
        {
            "client_id": CONNECTOR_CLIENT,
            "client_secret": CONNECTOR_CLIENT,
            "grant_type": "token",
            "token": "synthetic-connector-token",
        }
    ]
    sent = processing.calls[-1]
    assert str(sent.url) == TEST_URL + "/protected/documents/registration/receipts"
    assert sent.headers["authorization"] == "Bearer access-1"
    assert sent.headers["idempotency-key"] == "order-1"


async def test_access_token_is_reused_and_renewed_by_refresh_token_before_expiry():
    processing, clock = Processing(), Clock()
    client = bifit(processing, clock)
    await client.register(RECEIPT, "order-1")
    await client.register(RECEIPT, "order-2")
    assert len(processing.forms()) == 1
    clock.now += 3599 - TOKEN_RENEWAL_MARGIN_SECONDS
    await client.register(RECEIPT, "order-3")
    assert processing.forms()[-1] == {
        "client_id": CONNECTOR_CLIENT,
        "client_secret": CONNECTOR_CLIENT,
        "grant_type": "refresh_token",
        "refresh_token": "refresh-1",
    }
    assert processing.calls[-1].headers["authorization"] == "Bearer access-2"


async def test_rejected_refresh_token_falls_back_to_connector_token():
    processing, clock = Processing(), Clock()
    client = bifit(processing, clock)
    await client.register(RECEIPT, "order-1")
    processing.refresh_fails = True
    clock.now += 3600
    await client.register(RECEIPT, "order-2")
    assert [form["grant_type"] for form in processing.forms()] == ["token", "refresh_token", "token"]


async def test_unauthorized_request_is_repeated_once_with_new_token():
    processing = Processing()
    processing.unauthorized = 1
    assert await bifit(processing).register(RECEIPT, "order-1") == "254934"
    assert processing.calls[-1].headers["authorization"] == "Bearer access-2"
    processing.unauthorized = 2
    with pytest.raises(ApiError) as error:
        await bifit(processing).register(RECEIPT, "order-2")
    assert error.value.status == 502


@pytest.mark.parametrize("answer", [True, "254 934", {"id": 1}, None, -1])
async def test_unexpected_registration_answer_is_a_cashbox_failure(answer):
    processing = Processing()
    processing.document = answer
    with pytest.raises(ApiError) as error:
        await bifit(processing).register(RECEIPT, "order-1")
    assert error.value.status == 502


@pytest.mark.parametrize("key", ["", "ключ", "a" * 65, "order 1", None])
async def test_invalid_idempotency_key_is_rejected_before_any_request(key):
    processing = Processing()
    with pytest.raises(ApiError) as error:
        await bifit(processing).register(RECEIPT, key)
    assert error.value.status == 400 and processing.calls == []


async def test_document_status_is_read_by_id():
    processing = Processing()
    document = await bifit(processing).document("254934")
    assert document == {"status": "SUCCESS", "fiscalDocument": 5088}
    assert str(processing.calls[-1].url) == TEST_URL + "/protected/documents/254934"


@pytest.mark.parametrize("status", [{"status": "DONE"}, {}, [1], "SUCCESS"])
async def test_unknown_document_status_is_a_cashbox_failure(status):
    processing = Processing()
    processing.status = status
    with pytest.raises(ApiError) as error:
        await bifit(processing).document("254934")
    assert error.value.status == 502


@pytest.mark.parametrize("id", ["../token", "", "12a", 254934])
async def test_invalid_document_id_is_rejected_before_any_request(id):
    processing = Processing()
    with pytest.raises(ApiError) as error:
        await bifit(processing).document(id)
    assert error.value.status == 400 and processing.calls == []


@pytest.mark.parametrize("url", [TEST_URL, LIVE_URL, LIVE_URL + "/", ""])
def test_processing_address_accepts_only_bifit_https_hosts(url):
    assert settings(BIFIT_API_URL=url).BIFIT_API_URL == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://fp.bifit.com/processing-api",
        "https://fp.bifit.com.evil.test/processing-api",
        "https://user:pass@fp.bifit.com/processing-api",
        "https://fp.bifit.com:8443/processing-api",
        "https://fp.bifit.com/processing-api?x=1",
        "https://example.com/processing-api",
    ],
)
def test_processing_address_rejects_other_hosts_and_forms(url):
    with pytest.raises(ValidationError):
        settings(BIFIT_API_URL=url)


def test_production_requires_both_values_and_live_processing():
    production = {
        "APP_ENV": "production",
        "ADMIN_AUTH_SECRET": "synthetic-independent-office-secret-for-tests",
        "CHECKOUT_DATABASE_URL": "mariadb://api:test@mariadb/club",
        "PUBLIC_URL": "https://club.test",
        "SMTP_HOST": "smtp.test",
        "SMTP_FROM": "office@club.test",
        "OFFICE_NOTIFY_CHANNEL": "email",
        "OFFICE_EMAIL": "office@club.test",
    }
    assert not settings(**production).production_errors()
    assert not settings(**production, BIFIT_API_URL=LIVE_URL, BIFIT_CONNECTOR_TOKEN="synthetic").production_errors()
    assert (
        "BIFIT_API_URL и BIFIT_CONNECTOR_TOKEN задаются вместе"
        in settings(**production, BIFIT_API_URL=LIVE_URL).production_errors()
    )
    assert (
        "В production нужен боевой адрес БИФИТ, а не тестовый"
        in settings(**production, BIFIT_API_URL=TEST_URL, BIFIT_CONNECTOR_TOKEN="synthetic").production_errors()
    )
