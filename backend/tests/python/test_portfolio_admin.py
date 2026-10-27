from uuid import uuid4

import httpx
import pytest
from test_community_integration import account

PORTFOLIO = {"title": "Переговоры для юристов", "format": "blended", "duration": "6 недель", "price": 4500000}


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def create(client, headers, route, body):
    response = await client.post(route, headers=headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def trainer(client, headers, fio="Вымышленный Тренер"):
    return (await create(client, headers, "/admin/trainers", {"fio": fio}))["id"]


async def listed(client, headers, route, id):
    rows = (await client.get(route, headers=headers)).json()
    return next((row for row in rows if row["id"] == id), None)


async def test_portfolio_program_saves_card_fields_and_trainer_order(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        first, second = await trainer(client, editor, "Первый Тренер"), await trainer(client, editor, "Второй Тренер")
        body = {
            **PORTFOLIO,
            "audience": ["Юристы компаний"],
            "competencies": ["Подготовка позиции", "Ведение переговоров"],
            "communication": ["Telegram-чат", "Zoom"],
            "capacity": 20,
            "start": "1 декабря 2026",
            "trainer_ids": [second, first, second],
        }
        program = await create(client, editor, "/admin/portfolio", body)
        row = await listed(client, editor, "/admin/portfolio", program["id"])
        assert row["trainer_ids"] == [second, first]
        assert row["document"] == "Сертификат об обучении" and row["dates"] == {"start": "1 декабря 2026"}
        assert row["competencies"] == ["Подготовка позиции", "Ведение переговоров"]
        assert row["communication"] == ["Telegram-чат", "Zoom"] and row["capacity"] == 20
        assert (await app.state.store.one("programs", program["id"]))["catalog"] == "portfolio"


async def test_patch_replaces_trainers_only_when_sent(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        person = await trainer(client, editor)
        program = await create(client, editor, "/admin/portfolio", {**PORTFOLIO, "trainer_ids": [person]})
        route = "/admin/portfolio/" + program["id"]
        assert (await client.patch(route, headers=editor, json={"price": 5000000})).status_code == 200
        row = await listed(client, editor, "/admin/portfolio", program["id"])
        assert row["trainer_ids"] == [person] and row["price"] == 5000000
        assert (await client.patch(route, headers=editor, json={"trainer_ids": []})).status_code == 200
        assert (await listed(client, editor, "/admin/portfolio", program["id"]))["trainer_ids"] == []


async def test_unknown_trainer_is_rejected_without_saving(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        response = await client.post(
            "/admin/portfolio", headers=editor, json={**PORTFOLIO, "trainer_ids": [str(uuid4())]}
        )
        assert response.status_code == 400
        assert (await client.get("/admin/portfolio", headers=editor)).json() == []
        response = await client.post("/admin/portfolio", headers=editor, json={**PORTFOLIO, "trainer_ids": ["x"]})
        assert response.status_code == 400


async def test_deleted_trainer_leaves_programs(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        keep, gone = await trainer(client, editor, "Остаётся"), await trainer(client, editor, "Удаляется")
        program = await create(client, editor, "/admin/portfolio", {**PORTFOLIO, "trainer_ids": [gone, keep]})
        assert (await client.delete("/admin/trainers/" + gone, headers=editor)).status_code == 200
        assert (await listed(client, editor, "/admin/portfolio", program["id"]))["trainer_ids"] == [keep]
        assert [row["fio"] for row in (await client.get("/admin/trainers", headers=editor)).json()] == ["Остаётся"]


async def test_dpo_and_portfolio_admin_sections_are_isolated(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        program = await create(client, editor, "/admin/portfolio", PORTFOLIO)
        assert await listed(client, editor, "/admin/programs", program["id"]) is None
        dpo_route = "/admin/programs/" + program["id"]
        assert (await client.patch(dpo_route, headers=editor, json={"title": "Чужая правка"})).status_code == 404
        assert (await client.delete(dpo_route, headers=editor)).status_code == 404
        dpo = await create(
            client,
            editor,
            "/admin/programs",
            {"title": "Программа ДПО", "direction": "Право", "format": "online", "duration": "72 ч", "price": 0},
        )
        assert await listed(client, editor, "/admin/portfolio", dpo["id"]) is None
        assert (await client.delete("/admin/portfolio/" + dpo["id"], headers=editor)).status_code == 404
        assert (await client.delete("/admin/portfolio/" + program["id"], headers=editor)).status_code == 200
        assert (await app.state.store.one("programs", dpo["id"]))["catalog"] == "dpo"


async def test_portfolio_program_stays_out_of_public_dpo_and_cart(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        program = await create(client, editor, "/admin/portfolio", PORTFOLIO)
        assert (await client.get("/programs")).json() == []
        assert (await client.get("/programs/" + program["slug"])).status_code == 404
        assert (await client.get("/stats")).json()["programs"] == 0
        assert "/dpo/" + program["slug"] not in (await client.get("/sitemap.xml")).text
        response = await client.post(
            "/cart", headers={"x-cart-session": str(uuid4())}, json={"type": "dpo", "ref_id": program["slug"]}
        )
        assert response.status_code == 404


@pytest.mark.parametrize("route", ["/admin/trainers", "/admin/portfolio"])
async def test_portfolio_admin_requires_staff(database_app, route):
    app = database_app
    _, member = await account(app)
    async with client_for(app) as client:
        assert (await client.get(route)).status_code == 401
        assert (await client.get(route, headers=member)).status_code == 401
        assert (await client.post(route, headers=member, json={"fio": "Посторонний"})).status_code == 401


async def test_blank_document_falls_back_to_certificate(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        program = await create(client, editor, "/admin/portfolio", {**PORTFOLIO, "document": ""})
        assert (await listed(client, editor, "/admin/portfolio", program["id"]))["document"] == "Сертификат об обучении"
        route = "/admin/portfolio/" + program["id"]
        assert (await client.patch(route, headers=editor, json={"document": "Удостоверение"})).status_code == 200
        assert (await client.patch(route, headers=editor, json={"document": "  "})).status_code == 200
        assert (await listed(client, editor, "/admin/portfolio", program["id"]))["document"] == "Сертификат об обучении"
