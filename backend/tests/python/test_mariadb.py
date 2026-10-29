import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
from club_ops.bootstrap import BootstrapConfig, bootstrap
from club_ops.database_transfer import FORMAT, fingerprint, import_snapshot, model_tables, normalized, table_rows
from django.db import IntegrityError, models

from club_api.core.config import Settings
from club_api.main import create_app

REPO = Path(__file__).resolve().parents[3]
DELETE_RULES = {models.CASCADE: "CASCADE", models.SET_NULL: "SET NULL", models.PROTECT: "RESTRICT"}
FOREIGN_KEYS_SQL = (
    "SELECT k.TABLE_NAME AS table_name,k.COLUMN_NAME AS column_name,r.DELETE_RULE AS rule "
    "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
    "ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
    "WHERE k.TABLE_SCHEMA=DATABASE()"
)


def model_foreign_keys():
    return sorted(
        (model._meta.db_table, field.column, DELETE_RULES[field.remote_field.on_delete])
        for model in model_tables().values()
        for field in model._meta.local_fields
        if isinstance(field, models.ForeignKey)
    )


async def test_runtime_role_serves_crud_without_operator_permissions(database_app):
    app = database_app
    if app.state.database.vendor != "mysql":
        pytest.skip("Проверка прав MariaDB выполняется в отдельном сценарии")
    async with app.state.database.connection() as connection:
        await bootstrap(
            connection,
            BootstrapConfig(
                "production", False, "office@example.test", "synthetic-office-password", "https://club.example.test"
            ),
        )
    parsed = urlsplit(app.state.settings.secret("CHECKOUT_DATABASE_URL"))
    settings = Settings(
        AUTH_SECRET=app.state.settings.AUTH_SECRET,
        ADMIN_AUTH_SECRET=app.state.settings.ADMIN_AUTH_SECRET,
        CHECKOUT_DATABASE_URL=f"mariadb://club_api:synthetic-restricted-mariadb-test-only@{parsed.hostname}:{parsed.port}{parsed.path}",
        UPLOADS_PATH=app.state.settings.UPLOADS_PATH,
        JOBS_ENABLED="false",
    )
    restricted = create_app(settings)
    async with (
        restricted.lifespan(restricted),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=restricted), base_url="http://test") as client,
    ):
        login = await client.post(
            "/auth/admin-login", json={"email": "office@example.test", "password": "synthetic-office-password"}
        )
        assert login.status_code == 200
        headers = {"Authorization": "Bearer " + login.json()["token"]}
        assert (await client.get("/ready")).status_code == 200
        assert (await client.get("/admin/overview", headers=headers)).status_code == 200
        response = await client.post(
            "/admin/products",
            headers=headers,
            json={"title": "Проверка роли", "category": "Тест", "price": 100, "stock": 2},
        )
        assert response.status_code == 200
        id = response.json()["id"]
        assert (
            await client.patch("/admin/products/" + id, headers=headers, json={"title": "Обновлённый товар"})
        ).status_code == 200
        assert (await client.delete("/admin/products/" + id, headers=headers)).status_code == 200


async def test_transfer_preserves_cycles_json_and_private_fields(database_app, tmp_path):
    app = database_app
    if app.state.database.vendor != "mysql":
        pytest.skip("Перенос проверяется на MariaDB")
    from club_api.db.models import Alumnus, NewsInbox, PageViews, User, UserRole

    async with app.state.database.connection() as connection:

        def seed():
            first = UserRole.objects.create(name="alumni")
            second = UserRole.objects.create(name="editor", parent=first)
            first.parent = second
            first.save(update_fields=("parent",))
            user = User.objects.create(
                email="transfer@example.test",
                password="synthetic-private-hash",
                provider="external-sso",
                tfa_secret="synthetic-mfa",
                role=first,
            )
            member = Alumnus.objects.create(
                user=user, fio="Тестовый участник", contacts_json={"nested": [1, None, "текст"]}
            )
            invited = Alumnus.objects.create(fio="Второй участник", referred_by=member)
            member.referred_by = invited
            member.save(update_fields=("referred_by",))
            PageViews.objects.create(day="2026-10-02", path="/dpo/", hits=3)
            NewsInbox.objects.create(
                id="synthetic-source",
                source_url="https://source.example.test/news",
                sources=["telegram", "faculty"],
                title="Новость",
                published_at="2026-10-02T10:00:00+03:00",
            )
            return {name: normalized(table_rows(model)) for name, model in model_tables().items()}

        tables = await connection.run(seed)
        await connection.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            for name in tables:
                await connection.execute(f"TRUNCATE TABLE `{name}`")
        finally:
            await connection.execute("SET FOREIGN_KEY_CHECKS=1")
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(json.dumps({"format": FORMAT, "tables": tables}))
        snapshot.chmod(0o600)
        result = await connection.run(import_snapshot, snapshot)
        assert result["verified"]
        assert result["tables"] == {name: fingerprint(rows) for name, rows in tables.items()}
        with pytest.raises(ValueError, match="пустой"):
            await connection.run(import_snapshot, snapshot)


async def test_invalid_transfer_rolls_back_every_table(database_app, tmp_path):
    if database_app.state.database.vendor != "mysql":
        pytest.skip("Откат переноса проверяется на MariaDB")
    from club_api.db.models import Alumnus

    async with database_app.state.database.connection() as connection:

        def source():
            member = Alumnus.objects.create(fio="Тестовая запись")
            tables = {name: normalized(table_rows(model)) for name, model in model_tables().items()}
            member.delete()
            return tables

        tables = await connection.run(source)
        tables["alumni"][0]["user_id"] = str(uuid4())
        snapshot = tmp_path / "broken.json"
        snapshot.write_text(json.dumps({"format": FORMAT, "tables": tables}))
        snapshot.chmod(0o600)
        with pytest.raises(IntegrityError):
            await connection.run(import_snapshot, snapshot)
        assert not await connection.run(lambda: any(model.objects.exists() for model in model_tables().values()))


async def test_each_foreign_key_has_one_database_delete_rule(database_app):
    if database_app.state.database.vendor != "mysql":
        pytest.skip("Действия внешних ключей задают миграции MariaDB")
    rows = await database_app.state.database.rows(FOREIGN_KEYS_SQL)
    actual = sorted((row["table_name"], row["column_name"], row["rule"]) for row in rows)
    assert actual == model_foreign_keys()


async def test_unicode_case_search_and_literal_wildcards(database_app):
    if database_app.state.database.vendor != "mysql":
        pytest.skip("Сравнения MariaDB проверяются в отдельном сценарии")
    store = database_app.state.store
    await store.create("news", {"slug": "unicode", "title": "ПРАВО 50%_КЛУБ"})
    assert len(await store.read("news", filters={"title": {"_icontains": "право"}})) == 1
    assert not await store.read("news", filters={"title": {"_contains": "право"}})
    assert len(await store.read("news", filters={"title": {"_icontains": "%_клуб"}})) == 1
    assert not await store.read("news", filters={"title": {"_icontains": "51%_"}})


async def test_social_updates_are_idempotent_and_preserve_event_order(database_app):
    from club_api.modules.gamification.social import record_reaction

    state = database_app.state
    state.settings.TELEGRAM_REACTIONS_CHAT_ID = "-12345"
    member = await state.store.create(
        "alumni", {"fio": "Участник", "telegram_id": "12345", "verification_status": "verified"}
    )
    update = {
        "user": {"id": 12345},
        "chat": {"id": -12345},
        "message_id": 5,
        "date": 100,
        "new_reaction": [{"type": "emoji"}],
    }
    await record_reaction(state, update, 10)
    await record_reaction(state, {**update, "date": 99, "new_reaction": []}, 20)
    await record_reaction(state, {**update, "new_reaction": []}, 9)
    rows = await state.database.rows(
        "SELECT active,update_id FROM club_social_reactions WHERE alumni_id=%s", (member["id"],)
    )
    assert len(rows) == 1 and rows[0]["active"] and rows[0]["update_id"] == 10
    await record_reaction(state, {**update, "new_reaction": []}, 11)
    await record_reaction(state, {**update, "new_reaction": []}, 11)
    rows = await state.database.rows(
        "SELECT active,update_id FROM club_social_reactions WHERE alumni_id=%s", (member["id"],)
    )
    assert len(rows) == 1 and not rows[0]["active"] and rows[0]["update_id"] == 11


async def test_operator_cli_imports_with_django_async_dispatch(database_app, tmp_path):
    import asyncio
    import os
    import subprocess
    import sys

    if database_app.state.database.vendor != "mysql":
        pytest.skip("Команда импорта предназначена для MariaDB")
    from club_api.db.models import Alumnus

    async with database_app.state.database.connection() as connection:

        def source():
            member = Alumnus.objects.create(fio="Перенос через команду оператора")
            tables = {name: normalized(table_rows(model)) for name, model in model_tables().items()}
            member.delete()
            return tables

        tables = await connection.run(source)
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"format": FORMAT, "tables": tables}))
    path.chmod(0o600)
    environment = {**os.environ, "CHECKOUT_DATABASE_URL": database_app.state.settings.secret("CHECKOUT_DATABASE_URL")}
    environment["PYTHONPATH"] = str(REPO / "scripts")
    environment.pop("DATABASE_URL", None)
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "club_ops.cli", "import-postgres", str(path)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "таблиц 49, записей 1" in result.stdout
    async with database_app.state.database.connection() as connection:
        assert await connection.run(lambda: Alumnus.objects.get().fio) == "Перенос через команду оператора"
