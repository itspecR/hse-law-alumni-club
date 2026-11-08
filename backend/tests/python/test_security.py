import hashlib
import hmac
import json
import logging
import time
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from pydantic import ValidationError
from request_helpers import request_from_scope as Request

from club_api.core.config import Settings
from club_api.core.security import LoginAttempts, client_ip, trust_proxy, yookassa_ip
from club_api.db.store import (
    MAX_FILTER_DEPTH,
    MAX_FILTER_PARTS,
    MAX_FILTER_VALUES,
    Store,
    check_field,
    check_filters,
    columns,
)
from club_api.main import create_app
from club_api.modules.auth import passwords
from club_api.modules.auth.passwords import hash_password, verify_password
from club_api.modules.auth.routes import RegisterBody
from club_api.modules.telegram.signature import validate_init_data


def settings(**kwargs):
    return Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only", **kwargs)


@pytest.mark.parametrize(
    ("address", "hop", "trusted"),
    [
        ("172.18.0.8", 0, True),
        ("::ffff:10.5.0.1", 0, True),
        ("192.168.1.2", 0, True),
        ("172.18.0.8", 1, False),
        ("127.0.0.1", 0, False),
        ("8.8.8.8", 0, False),
        ("::1", 0, False),
    ],
)
def test_single_proxy_hop(address, hop, trusted):
    assert trust_proxy(address, hop) is trusted


def test_forwarded_ip_uses_nearest_hop():
    def request(peer, forwarded):
        return Request({"type": "http", "client": (peer, 3000), "headers": [(b"x-forwarded-for", forwarded.encode())]})

    assert client_ip(request("172.18.0.4", "185.71.76.5, 203.0.113.5")) == "203.0.113.5"
    assert client_ip(request("127.0.0.1", "185.71.76.5")) == "127.0.0.1"
    assert client_ip(request("172.18.0.4", "invalid")) == "172.18.0.4"


@pytest.mark.parametrize(
    ("address", "allowed"),
    [
        ("185.71.76.31", True),
        ("185.71.76.32", False),
        ("::ffff:77.75.156.11", True),
        ("77.75.156.12", False),
        ("2a02:5180::1", True),
        ("2a02:5181::1", False),
        ("999.1.2.3", False),
    ],
)
def test_payment_sender_networks(address, allowed):
    assert yookassa_ip(address) is allowed


OWNER_IP, ATTACKER_IP = "203.0.113.2", "203.0.113.1"


def test_account_lock_holds_only_for_the_failing_address():
    now = [1000]
    attempts = LoginAttempts(clock=lambda: now[0])
    for _ in range(LoginAttempts.ACCOUNT_LIMIT):
        attempts.fail("member@example.test", ATTACKER_IP)
    assert attempts.locked("MEMBER@example.test", ATTACKER_IP)
    assert not attempts.locked("member@example.test", OWNER_IP)
    assert not attempts.locked("other@example.test", ATTACKER_IP)
    for index in range(LoginAttempts.ADDRESS_LIMIT - LoginAttempts.ACCOUNT_LIMIT):
        attempts.fail(f"member{index}@example.test", ATTACKER_IP)
    assert attempts.locked("unrelated@example.test", ATTACKER_IP)
    assert not attempts.locked("unrelated@example.test", OWNER_IP)
    now[0] += LoginAttempts.ADDRESS_LOCK_SECONDS + 1
    assert not attempts.locked("member@example.test", ATTACKER_IP)


def test_own_login_does_not_reset_address_failures():
    attempts = LoginAttempts(clock=lambda: 1000)
    for index in range(LoginAttempts.ADDRESS_LIMIT - 1):
        attempts.fail(f"member{index}@example.test", ATTACKER_IP)
    attempts.success("own@example.test", ATTACKER_IP)
    attempts.fail("member@example.test", ATTACKER_IP)
    assert attempts.locked("own@example.test", ATTACKER_IP)


def test_successful_login_clears_only_its_own_account_counter():
    attempts = LoginAttempts(clock=lambda: 1000)
    for _ in range(LoginAttempts.ACCOUNT_LIMIT - 1):
        attempts.fail("member@example.test", OWNER_IP)
    attempts.success("member@example.test", OWNER_IP)
    attempts.fail("member@example.test", OWNER_IP)
    assert not attempts.locked("member@example.test", OWNER_IP)


@pytest.mark.parametrize(
    "name", ["password", "token", "tfa_secret", "auth_data", "provider", 'id"; DROP TABLE alumni; --']
)
def test_secret_columns_rejected(name):
    with pytest.raises(ValueError):
        check_field("directus_users", name)
    with pytest.raises(ValueError):
        check_filters("directus_users", {name: {"_eq": "x"}})
    assert name not in columns("directus_users")


@pytest.mark.asyncio
async def test_unknown_tables_and_staff_mutations_rejected():
    store = Store(None)
    for table in ('orders"; DROP TABLE alumni; --', "club_settings", "constructor"):
        with pytest.raises(ValueError):
            await store.read(table)
        with pytest.raises(ValueError):
            await store.aggregate(table)
    for table in ("directus_users", "directus_roles", "directus_files"):
        with pytest.raises(ValueError):
            await store.create(table, {})
        with pytest.raises(ValueError):
            await store.update(table, {"status": "active"}, id="x")
        with pytest.raises(ValueError):
            await store.delete(table, id="x")


def nested(depth):
    filters = {"title": {"_eq": "x"}}
    for _ in range(depth):
        filters = {"_and": [filters]}
    return filters


@pytest.mark.parametrize(
    "filters",
    (
        None,
        {"title": {"_icontains": "Robert'); DROP TABLE programs; --%_"}},
        {"title": {"_eq": None, "_neq": "x", "_gte": 1}},
        {"_or": [{"title": {"_null": True}}, {"slug": {"_in": ["a", None], "_nin": []}}]},
        {"_and": []},
        {"title": {"_in": ["x"] * MAX_FILTER_VALUES}},
        {"_or": [{}] * MAX_FILTER_PARTS},
        nested(MAX_FILTER_DEPTH),
    ),
)
def test_filter_shapes_within_limits_are_accepted(filters):
    check_filters("programs", filters)


@pytest.mark.parametrize(
    "filters",
    (
        [],
        {"title": "x"},
        {"title": {"_eq": ["x"]}},
        {"title": {"_null": "yes"}},
        {"title": {"_in": "x"}},
        {"title": {"_in": [["x"]]}},
        {"title": {"_in": ["x"] * (MAX_FILTER_VALUES + 1)}},
        {"title": {"_contains": 1}},
        {"title": {"_regex": ".*"}},
        {"_or": {"title": {"_eq": "x"}}},
        {"_or": [{}] * (MAX_FILTER_PARTS + 1)},
        nested(MAX_FILTER_DEPTH + 1),
    ),
)
def test_filter_shapes_beyond_rules_are_rejected(filters):
    with pytest.raises(ValueError):
        check_filters("programs", filters)


@pytest.mark.asyncio
async def test_bcrypt_sha256_settings_and_failure():
    hashed = await hash_password("synthetic-account-password")
    assert hashed.startswith("bcrypt_sha256$$2b$12$")
    assert await verify_password(hashed, "synthetic-account-password")
    assert not await verify_password(hashed, "incorrect")
    assert not await verify_password("$argon2id$broken", "incorrect")
    assert not await verify_password("plaintext", "plaintext")


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [None, "plaintext", "bcrypt_sha256$$2b$99$synthetic", "bcrypt_sha256$"])
async def test_unusable_hash_still_costs_one_check(monkeypatch, encoded):
    checks = []
    monkeypatch.setattr(passwords, "decoy", lambda: "synthetic-decoy")
    monkeypatch.setattr(passwords, "HASHER", SimpleNamespace(verify=lambda _password, value: checks.append(value)))
    assert not await verify_password(encoded, "synthetic")
    assert checks == ["synthetic-decoy"]


@pytest.mark.asyncio
async def test_argon2_hash_is_no_longer_accepted():
    argon2 = "$argon2id$v=19$m=65536,p=1,t=3$BYDIDml/fyYTrmElgyiOKw$ggVfFB3RKgSkEsAuWT0KnFXgnB/kjUgHZJh1aldpCeA"
    assert not await verify_password(argon2, "synthetic-cross-runtime")


def test_production_config_requires_separate_secrets_and_delivery():
    invalid = settings(
        APP_ENV="production", ADMIN_AUTH_SECRET="synthetic-session-secret-for-tests-only", SEED_DEMO="true"
    )
    errors = " ".join(invalid.production_errors())
    assert all(
        name in errors
        for name in ("ADMIN_AUTH_SECRET", "SMTP_HOST", "PUBLIC_URL", "SEED_DEMO", "CHECKOUT_DATABASE_URL")
    )
    valid = settings(
        APP_ENV="production",
        ADMIN_AUTH_SECRET="synthetic-independent-office-secret-for-tests",
        CHECKOUT_DATABASE_URL="mariadb://api:test@mariadb/club",
        PUBLIC_URL="https://club.test",
        SMTP_HOST="smtp.test",
        SMTP_FROM="office@club.test",
        OFFICE_NOTIFY_CHANNEL="email",
        OFFICE_EMAIL="office@club.test",
    )
    assert not valid.production_errors()
    assert "synthetic-independent" not in repr(valid)


def test_telegram_signed_data_and_freshness():
    issued = int(time.time())
    values = {"auth_date": str(issued), "user": json.dumps({"id": 12345})}
    secret = hmac.new(b"WebAppData", b"synthetic-telegram-token", hashlib.sha256).digest()
    signature = hmac.new(
        secret, "\n".join(f"{key}={value}" for key, value in sorted(values.items())).encode(), hashlib.sha256
    ).hexdigest()
    data = urlencode({**values, "hash": signature})
    assert validate_init_data(data, "synthetic-telegram-token") == {"id": 12345}
    assert validate_init_data(data + "&user={}", "synthetic-telegram-token") is None
    assert validate_init_data(data, "wrong-token") is None
    assert validate_init_data(data, "synthetic-telegram-token", now=issued + 86401) is None
    assert validate_init_data(data, "synthetic-telegram-token", now=issued - 61) is None


@pytest.mark.asyncio
async def test_http_security_limits_logs_and_body(caplog, tmp_path):
    app = create_app(settings(UPLOADS_PATH=tmp_path))
    caplog.set_level(logging.INFO, logger="club.http")
    async with (
        app.lifespan(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client,
    ):
        health = await client.get("/health?token=never-log-query", headers={"authorization": "Bearer never-log-header"})
        assert health.status_code == 200
        assert health.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert "never-log" not in caplog.text
        ready = await client.get("/ready")
        assert ready.status_code == 503 and ready.json() == {"status": "degraded"}
        assert ready.headers["cache-control"] == "no-store"
        invalid = await client.post("/auth/login", json={"email": "bad", "password": "secret"})
        assert invalid.status_code == 400 and "secret" not in invalid.text
        for _ in range(4):
            assert (await client.post("/auth/login", json={})).status_code == 400
        assert (await client.post("/auth/login", json={})).status_code == 429
        assert (await client.post("/auth/register", content=b"x" * (256 * 1024 + 1))).status_code == 413

        async def oversized():
            yield b"x" * (128 * 1024)
            yield b"x" * (128 * 1024 + 1)

        assert (await client.post("/auth/register", content=oversized())).status_code == 413
        assert (await client.get("/openapi.json")).status_code == 404
        assert (
            await client.options(
                "/auth/login", headers={"origin": "https://web.telegram.org", "access-control-request-method": "POST"}
            )
        ).headers["access-control-allow-origin"] == "https://web.telegram.org"
        assert (
            "access-control-allow-origin"
            not in (await client.get("/health", headers={"origin": "https://evil.test"})).headers
        )


@pytest.mark.parametrize("consent", [False, 1, "true"])
def test_explicit_boolean_consent(consent):
    body = {
        "fio": "Тестовая анкета",
        "email": "test@example.test",
        "password": "synthetic-password",
        "cohort": "2020",
        "edu_level": "магистратура",
        "edu_program": "Право",
        "consent_pdn": consent,
    }
    with pytest.raises(ValidationError):
        RegisterBody.model_validate(body)


@pytest.mark.asyncio
async def test_disconnected_request_is_not_logged_as_server_error(caplog):
    from club_api.core.http import SecurityMiddleware

    async def application(scope, receive, send):
        assert (await receive())["type"] == "http.request"
        assert (await receive())["type"] == "http.disconnect"

    messages = iter([{"type": "http.request", "body": b""}, {"type": "http.disconnect"}])

    async def receive():
        return next(messages)

    sent = []

    async def send(message):
        sent.append(message)

    caplog.set_level(logging.INFO, logger="club.http")
    middleware = SecurityMiddleware(
        application, Settings(AUTH_SECRET="synthetic-disconnect-session-secret-for-tests-only"), {}
    )
    await middleware(
        {"type": "http", "method": "GET", "path": "/me", "headers": [], "client": ("127.0.0.1", 1)}, receive, send
    )
    assert not sent
    assert "GET /me disconnected" in caplog.text
    assert "GET /me 500" not in caplog.text
