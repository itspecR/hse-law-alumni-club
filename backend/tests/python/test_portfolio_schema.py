from uuid import uuid4

import httpx
import psycopg
import pytest
from django.apps import apps
from django.db import IntegrityError
from test_community_integration import account, png

from club_api.db.schema import DATA_COLUMNS, JSON_COLUMNS

CONSTRAINT_ERRORS = (IntegrityError, psycopg.IntegrityError)


def model_columns():
    return {
        model._meta.db_table: {field.column for field in model._meta.local_fields}
        for model in apps.get_app_config("club_data").get_models()
    }


async def program(app, **extra):
    return await app.state.store.create("programs", {"slug": str(uuid4()), "title": "Программа клуба", **extra})


async def trainer(app, **extra):
    return await app.state.store.create("trainers", {"fio": "Вымышленный Тренер Тренерович", **extra})


async def links(app, filters):
    return await app.state.store.read("program_trainers", filters=filters, fields=("id",), limit=-1)


def test_data_columns_exist_in_models():
    columns = model_columns()
    for table, names in DATA_COLUMNS.items():
        assert set(names) <= columns[table], table
    for table, names in JSON_COLUMNS.items():
        assert set(names) <= set(DATA_COLUMNS[table]), table


async def test_existing_programs_stay_in_dpo_catalog(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/admin/programs",
            headers=editor,
            json={"title": "Программа ДПО", "direction": "Право", "format": "online", "duration": "72 ч", "price": 0},
        )
        assert response.status_code == 200, response.text
        row = await app.state.store.one("programs", response.json()["id"])
        assert (row["catalog"], row["competencies"], row["communication"]) == ("dpo", None, None)
        assert (await client.get("/programs")).status_code == 200


async def test_portfolio_program_keeps_lists(database_app):
    app = database_app
    row = await program(
        app, catalog="portfolio", competencies=["Переговоры", "Анализ"], communication=["Telegram", "Zoom"]
    )
    saved = await app.state.store.one("programs", row["id"])
    assert saved["catalog"] == "portfolio"
    assert saved["competencies"] == ["Переговоры", "Анализ"] and saved["communication"] == ["Telegram", "Zoom"]


async def test_unknown_catalog_is_rejected(database_app):
    with pytest.raises(CONSTRAINT_ERRORS):
        await program(database_app, catalog="unknown")


async def test_trainer_defaults_to_draft(database_app):
    app = database_app
    row = await app.state.store.one("trainers", (await trainer(app))["id"])
    assert row["status"] == "draft" and row["created_at"] is not None


async def test_trainer_link_is_unique(database_app):
    app = database_app
    first, person = (await program(app), await trainer(app))
    await app.state.store.create("program_trainers", {"program_id": first["id"], "trainer_id": person["id"]})
    with pytest.raises(CONSTRAINT_ERRORS):
        await app.state.store.create("program_trainers", {"program_id": first["id"], "trainer_id": person["id"]})


async def test_deleting_program_or_trainer_removes_links(database_app):
    app = database_app
    first, second, person = (await program(app), await program(app), await trainer(app))
    for item in (first, second):
        await app.state.store.create("program_trainers", {"program_id": item["id"], "trainer_id": person["id"]})
    await app.state.store.delete("programs", id=first["id"])
    assert len(await links(app, {"trainer_id": {"_eq": person["id"]}})) == 1
    await app.state.store.delete("trainers", id=person["id"])
    assert await links(app, {"program_id": {"_eq": second["id"]}}) == []
    assert (await app.state.store.one("programs", second["id"]))["id"] == second["id"]


async def test_trainer_photo_is_protected_and_public_only_when_published(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        upload = await client.post("/admin/media", headers=editor, files={"file": ("photo.png", png(), "image/png")})
        assert upload.status_code == 201, upload.text
        id = upload.json()["id"]
        person = await trainer(app, photo=id)
        assert (await client.get(f"/media/{id}")).status_code == 404
        await app.state.store.update("trainers", {"status": "published"}, id=person["id"])
        assert (await client.get(f"/media/{id}")).status_code == 200
        assert (await client.delete(f"/admin/media/{id}", headers=editor)).status_code == 409
        await app.state.store.update("trainers", {"photo": None}, id=person["id"])
        assert (await client.delete(f"/admin/media/{id}", headers=editor)).status_code == 200
