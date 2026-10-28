import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import psycopg
import pytest
from club_ops.bootstrap import BootstrapConfig, OperatorError, bootstrap
from club_ops.catalog import retain_copy
from club_ops.staff import manage_staff
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import SecretStr
from test_community_integration import account

from club_api.core.config import Settings
from club_api.main import create_app
from club_api.modules.auth import passwords
from club_api.modules.catalog.sync import map_item, parse_initial_state, plan_sync
from club_api.modules.news.sources import article_date, parse_source
from club_api.observability.errors import scrub_event


def test_all_legacy_routes_are_present():
    create_app(Settings(AUTH_SECRET="synthetic-session-secret-for-tests-only"))
    from club_api.urls import api

    actual = {
        (method, "/" + route)
        for route, operations in api.default_router.path_operations.items()
        for operation in operations.operations
        for method in operation.methods
        if method != "HEAD"
    }
    old = json.loads(Path(__file__).with_name("legacy-routes.json").read_text())
    expected = {(row["method"], row["path"]) for row in old}
    added = {("GET", "/auth/admin-session"), ("POST", "/support/ask")}
    portfolio = {
        (method, "/admin/" + name + suffix)
        for name in ("trainers", "portfolio", "cohorts")
        for method, suffix in (("GET", ""), ("POST", ""), ("PATCH", "/{id}"), ("DELETE", "/{id}"))
    }
    public = {
        ("GET", "/portfolio"),
        ("GET", "/portfolio/{slug}"),
        ("POST", "/portfolio/{slug}/applications"),
        ("POST", "/portfolio/{slug}/quote"),
        ("GET", "/portfolio/challenge"),
    }
    promo = {
        (method, "/admin/promo-codes" + suffix)
        for method, suffix in (("GET", ""), ("POST", ""), ("PATCH", "/{id}"), ("DELETE", "/{id}"))
    }
    office = {
        ("PATCH", "/admin/orders/{id}/discount"),
        ("PATCH", "/admin/orders/{id}/cohort"),
    }
    assert actual == expected | added | portfolio | public | promo | office and len(actual) == 142


def test_hse_state_dates_and_manual_fields():
    source = 'window.__INITIAL_STATE__={items:[{id:42,title:"Тестовая программа",startDate:new Date(1796112000000),studyFormat:{title:"Онлайн"},educationPricing:100.5,__proto__:null}],total:1,pageSize:20};window.__URQL_DATA__={};'
    parsed = parse_initial_state(source)
    card = map_item(parsed["items"][0])
    assert card["priceKop"] == 10100 and card["start"] == "1 декабря 2026 г."
    changes = plan_sync(
        [{**card, "enrollment": "actual"}],
        [
            {
                "id": "same",
                "hse_id": "42",
                "slug": "same",
                "title": card["title"],
                "status": "published",
                "price": 9000,
                "source_url": None,
            }
        ],
    )
    assert len(changes) == 1 and changes[0]["kind"] == "update"
    assert "description" not in changes[0]["data"] and "title" not in changes[0]["data"]
    incoming = {"hse_id": "42", "description": "Импорт", "audience": ["Из источника"]}
    assert retain_copy(incoming, {"hse_id": "42", "description": "Текст офиса", "audience": []})["audience"] == []


def test_news_dates_dedup_and_sentry_privacy():
    html = '<div class="tgme_widget_message" data-post="AlumniLawHSE/42"><div class="tgme_widget_message_text">Название новости<br><a href="https://pravo.hse.ru/news/42.html?utm_source=tg">Источник</a></div><time datetime="2026-09-30T12:00:00Z"></time></div>'
    assert parse_source(html, "telegram")[0]["source_url"] == "https://pravo.hse.ru/news/42.html"
    assert article_date('<meta property="article:published_time" content="2026-09-30T12:00:00Z">').startswith(
        "2026-09-30"
    )
    assert article_date("30 сентября") is None
    event = scrub_event(
        {
            "message": "request_failed",
            "request": {"data": "ФИО и переписка"},
            "user": {"email": "synthetic@example.test"},
            "breadcrumbs": [{"data": "secret"}],
            "exception": {"values": ["query"]},
        },
        {},
    )
    assert event == {
        "message": "request_failed",
        "event_id": None,
        "timestamp": None,
        "level": "error",
        "platform": "python",
    }
    assert scrub_event({"message": "Произвольная строка"}, {}) is None


async def test_argon_cancel_holds_memory_slot(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow_hash(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return "synthetic"

    monkeypatch.setattr(passwords, "HASHER", SimpleNamespace(hash=slow_hash))
    slots = passwords.password_slots()
    await slots.acquire()
    task = asyncio.create_task(passwords.hash_password("synthetic"))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0.01)
    assert slots.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    slots.release()


async def test_bootstrap_preserves_accounts_settings_and_empty_home(database_app):
    db = database_app.state.database
    config = BootstrapConfig(
        "production", False, "bootstrap@example.test", "synthetic-bootstrap-password", "https://club.example.test"
    )
    async with db.connection() as connection:
        first = await bootstrap(connection, config)
        assert first["createdUsers"] == 1 and first["createdHome"]
        await connection.execute("UPDATE directus_users SET provider='existing-sso',tfa_secret='synthetic-mfa'")
        await connection.execute("UPDATE club_settings SET value='{}'")
        await connection.execute("DELETE FROM pages_blocks")
        before = await (await connection.execute("SELECT * FROM directus_users ORDER BY id")).fetchall()
        repeated = await bootstrap(connection, config)
        after = await (await connection.execute("SELECT * FROM directus_users ORDER BY id")).fetchall()
        assert before == after and repeated["createdUsers"] == 0
        assert await (await connection.execute("SELECT id FROM pages_blocks")).fetchall() == []
        assert (await (await connection.execute("SELECT value FROM club_settings WHERE \"key\"='site'")).fetchone())[
            "value"
        ] == {}
        with pytest.raises(OperatorError):
            await manage_staff(
                connection,
                action="reset-password",
                address=config.admin_email,
                role="admin",
                password="synthetic-changed-password",
            )


async def test_staff_reset_revokes_previous_session(database_app):
    app = database_app
    config = BootstrapConfig(
        "production", False, "bootstrap@example.test", "synthetic-bootstrap-password", "https://club.example.test"
    )
    async with app.state.database.connection() as connection:
        await bootstrap(connection, config)
        await manage_staff(
            connection,
            action="create",
            address="editor@example.test",
            role="editor",
            password="synthetic-original-password",
        )
        user = await app.state.auth.user(email="editor@example.test")
        token = app.state.auth.staff_session(str(user["id"]), "editor")
        await manage_staff(
            connection,
            action="reset-password",
            address="editor@example.test",
            role="editor",
            password="synthetic-changed-password",
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/admin/overview", headers={"Authorization": "Bearer " + token})).status_code == 401


async def test_podcast_subscription_replay_and_access(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    podcast = await app.state.store.create(
        "podcasts",
        {
            "title": "Тестовый выпуск",
            "is_free": False,
            "status": "published",
            "audio_url": "https://audio.example.test/test.mp3",
        },
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/podcasts")).json()["items"][0]["audio_url"] is None
        results = await asyncio.gather(*(client.post("/podcasts/subscribe", headers=headers) for _ in range(2)))
        assert all(row.status_code == 200 for row in results)
        assert len({row.json()["number"] for row in results}) == 1
        assert (await app.state.store.one("alumni", alumni_id))["podcast_sub_until"] is None
        await app.state.store.update("alumni", {"podcast_sub_until": "2099-01-01T00:00:00Z"}, id=alumni_id)
        link = (await client.get("/podcasts", headers=headers)).json()["items"][0]["audio_url"].removeprefix("/api")
        assert (await client.get(link)).status_code == 302
        assert (await client.get(link)).status_code == 302
        assert len(await app.state.store.read("podcast_plays", fields=("id",))) == 1
        await app.state.store.update("alumni", {"podcast_sub_until": "2020-01-01T00:00:00Z"}, id=alumni_id)
        assert (await client.get(link)).status_code == 403
        assert (await client.get(f"/podcasts/{podcast['id']}/audio?h=free&exp=99&sig=" + "a" * 64)).status_code == 403


async def test_telegram_secret_and_updates(database_app, monkeypatch):
    app = database_app
    app.state.settings.TELEGRAM_BOT_TOKEN = SecretStr("synthetic-bot-token")
    app.state.settings.TELEGRAM_WEBHOOK_SECRET = SecretStr("synthetic-webhook-secret")
    calls = []

    async def handle(body):
        calls.append(body)

    monkeypatch.setattr(app.state.telegram, "safe_handle", handle)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        body = {
            "update_id": 1,
            "message": {"message_id": 2, "chat": {"id": 3, "type": "private"}, "text": "/start", "from": {"id": 3}},
        }
        assert (await client.post("/telegram/webhook", json=body)).status_code == 403
        assert (
            await client.post(
                "/telegram/webhook", json=body, headers={"x-telegram-bot-api-secret-token": "synthetic-webhook-secret"}
            )
        ).status_code == 200
    assert len(calls) == 1 and calls[0]["message"]["from"]["id"] == 3


async def test_runtime_sql_role_cannot_change_roles_or_read_settings(database_app):
    app = database_app
    url = urlsplit(app.state.settings.secret("CHECKOUT_DATABASE_URL"))
    if app.state.database.vendor == "mysql":
        from django.db import DatabaseError

        from club_api.db.pool import Database

        database = Database(
            Settings(
                AUTH_SECRET="synthetic-session-secret-for-tests-only",
                CHECKOUT_DATABASE_URL=f"mariadb://club_api:synthetic-restricted-mariadb-test-only@{url.hostname}:{url.port}{url.path}",
            )
        )
        async with database.connection() as connection:
            for query in (
                "SELECT value FROM club_settings",
                "UPDATE directus_users SET role=NULL",
                "SELECT token FROM directus_users",
                "UPDATE directus_roles SET name='admin'",
            ):
                with pytest.raises(DatabaseError):
                    await connection.execute(query)
            assert (
                await (await connection.execute("SELECT id,email,password,role FROM directus_users")).fetchall() == []
            )
        return
    async with await AsyncConnection.connect(
        f"postgres://club_api:restricted-test-only@{url.hostname}:{url.port}{url.path}",
        row_factory=dict_row,
        autocommit=True,
    ) as connection:
        for query in (
            "SELECT value FROM club_settings",
            "UPDATE directus_users SET role=NULL",
            "SELECT token FROM directus_users",
            "UPDATE directus_roles SET name='admin'",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await connection.execute(query)
        assert await (await connection.execute("SELECT id,email,password,role FROM directus_users")).fetchall() == []


async def test_product_with_reservation_cannot_be_deleted_or_replace_stock(database_app):
    from psycopg.types.json import Jsonb

    app = database_app
    _, headers = await account(app, "admin")
    product = await app.state.store.create(
        "products", {"slug": "reserved", "title": "Товар с резервом", "category": "Тест", "price": 100, "stock": 1}
    )
    order = await app.state.store.create("orders", {"number": "ALU-2026-000001", "type": "merch", "status": "new"})
    await app.state.database.execute(
        "INSERT INTO club_checkout_commits(key_hash,request_hash,order_id,reservations,receipt) VALUES('synthetic-key','synthetic-request',%s,%s,'{}')",
        (order["id"], Jsonb([{"id": product["id"], "sku": None, "qty": 1}])),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        for method, body in (("DELETE", None), ("PATCH", {"stock": 100}), ("PATCH", {"variants_json": []})):
            response = await client.request(method, "/admin/products/" + product["id"], headers=headers, json=body)
            assert response.status_code == 409
        assert (
            await client.patch("/admin/products/" + product["id"], headers=headers, json={"title": "Новое название"})
        ).status_code == 200
        await app.state.checkout.change_status(order["id"], "canceled")
        assert (await client.delete("/admin/products/" + product["id"], headers=headers)).status_code == 200


async def test_jobs_are_scheduled_once_without_overlap(monkeypatch):
    from club_api.jobs import runner

    started, finish = asyncio.Event(), asyncio.Event()
    calls = []

    async def operation():
        calls.append("called")
        started.set()
        await finish.wait()

    monkeypatch.setattr(runner, "scheduled", lambda _state: [("synthetic-job", lambda _now: True, operation)])
    jobs = runner.Jobs(SimpleNamespace())
    instant = __import__("datetime").datetime(2026, 10, 1, 3, 0)
    jobs.tick(instant)
    await started.wait()
    jobs.tick(instant)
    jobs.tick(instant.replace(minute=1))
    assert calls == ["called"]
    finish.set()
    await asyncio.gather(*jobs.running.values())
    jobs.tick(instant.replace(minute=2))
    await asyncio.gather(*jobs.running.values())
    assert calls == ["called", "called"]
