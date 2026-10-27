from uuid import uuid4

import httpx


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def program(app, title, **extra):
    data = {"slug": str(uuid4()), "title": title, "price": 100000, "catalog": "portfolio", "status": "published"}
    return await app.state.store.create("programs", {**data, **extra})


async def trainer(app, fio, status="published"):
    return await app.state.store.create("trainers", {"fio": fio, "status": status, "bio": "Справка " + fio})


async def link(app, program_row, trainer_row, sort):
    await app.state.store.create(
        "program_trainers", {"program_id": program_row["id"], "trainer_id": trainer_row["id"], "sort": sort}
    )


async def test_list_shows_published_club_programs_open_first(database_app):
    app = database_app
    await program(app, "Б программа")
    await program(app, "А закрытая", enrollment="nonactual")
    await program(app, "В программа")
    await program(app, "Черновик", status="draft")
    await program(app, "Программа ДПО", catalog="dpo")
    async with client_for(app) as client:
        rows = (await client.get("/portfolio")).json()
    assert [row["title"] for row in rows] == ["Б программа", "В программа", "А закрытая"]
    assert "status" not in rows[0] and "trainer_ids" not in rows[0] and "competencies" not in rows[0]


async def test_card_shows_published_trainers_in_saved_order(database_app):
    app = database_app
    item = await program(app, "Переговоры", competencies=["Позиция"], communication=["Zoom"], audience=["Юристы"])
    first, second, hidden = (
        await trainer(app, "Первый"),
        await trainer(app, "Второй"),
        await trainer(app, "Скрытый", "draft"),
    )
    await link(app, item, second, 0)
    await link(app, item, hidden, 1)
    await link(app, item, first, 2)
    async with client_for(app) as client:
        card = (await client.get("/portfolio/" + item["slug"])).json()
    assert [row["fio"] for row in card["trainers"]] == ["Второй", "Первый"]
    assert set(card["trainers"][0]) == {"id", "fio", "photo", "bio"}
    assert (card["competencies"], card["communication"], card["audience"]) == (["Позиция"], ["Zoom"], ["Юристы"])
    assert "status" not in card


async def test_card_hides_drafts_dpo_and_unknown_programs(database_app):
    app = database_app
    draft = await program(app, "Черновик", status="draft")
    dpo = await program(app, "Программа ДПО", catalog="dpo")
    async with client_for(app) as client:
        for slug in (draft["slug"], dpo["slug"], "missing"):
            assert (await client.get("/portfolio/" + slug)).status_code == 404
