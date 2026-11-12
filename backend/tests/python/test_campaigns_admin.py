import json
from pathlib import Path

import httpx
import pytest
from test_campaigns import campaign, donation, trainer
from test_community_integration import account

BODY = {
    "slug": "intensive",
    "title": "Искусство влияния",
    "subtitle": "Имидж, речь, аудитория",
    "lede": "Клуб собирает средства на оснащение аудитории.",
    "date_text": "Дата уточняется",
    "goal_title": "Оснащение аудитории",
    "goal_amount": 150000000,
    "extra_amount": 1200000,
    "invite_threshold": 1500000,
    "amounts": [5000000, 500000, 1500000],
    "highlights": [{"value": "6", "label": "часов практики"}],
    "modules": [
        {"title": "Сильное выступление", "time": "10:00-11:30", "note": "Структура", "points": ["Цель"]},
        {"title": "Перерыв 15 минут", "time": None, "note": None, "points": []},
    ],
    "results": ["Строить выступление под аудиторию"],
    "steps": [{"title": "Пожертвование", "text": "Вы поддерживаете сбор на любую сумму."}],
    "faq": [{"question": "Это плата за участие?", "answer": "Нет."}],
    "note": "Участие в интенсиве бесплатное.",
    "payment_purpose": "  на оснащение   аудитории факультета права НИУ ВШЭ ",
    "invite_subject": "Приглашение на интенсив",
    "invite_body": "Текст приглашения",
}


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def test_editor_creates_edits_and_publishes_campaign(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    first, second = await trainer(app, "Первый"), await trainer(app, "Второй")
    async with client_for(app) as client:
        created = await client.post(
            "/admin/campaigns", headers=editor, json={**BODY, "trainer_ids": [second["id"], first["id"]]}
        )
        assert created.status_code == 200, created.text
        hidden = await client.get("/campaigns/intensive")
        [listed] = (await client.get("/admin/campaigns", headers=editor)).json()
        patched = await client.patch(
            "/admin/campaigns/" + listed["id"],
            headers=editor,
            json={"status": "published", "trainer_ids": [first["id"]], "goal_amount": 2400000},
        )
        assert patched.status_code == 200, patched.text
        shown = (await client.get("/campaigns/intensive")).json()
        [again] = (await client.get("/admin/campaigns", headers=editor)).json()
    assert hidden.status_code == 404
    assert (listed["status"], listed["trainer_ids"]) == ("draft", [second["id"], first["id"]])
    assert (listed["raised"], listed["percent"], listed["amounts"]) == (1200000, 0, [500000, 1500000, 5000000])
    assert listed["payment_purpose"] == "на оснащение аудитории факультета права НИУ ВШЭ"
    assert (listed["invite_body"], listed["modules"]) == (BODY["invite_body"], BODY["modules"])
    assert [row["fio"] for row in shown["speakers"]] == ["Первый"]
    assert (shown["goal_amount"], shown["raised"], shown["percent"]) == (2400000, 1200000, 50)
    assert (shown["title"], shown["faq"], shown["steps"]) == (BODY["title"], BODY["faq"], BODY["steps"])
    assert (again["status"], again["trainer_ids"], again["lede"]) == ("published", [first["id"]], BODY["lede"])
    events = await app.state.database.rows("SELECT event FROM audit_log ORDER BY event")
    assert [row["event"] for row in events if row["event"].startswith("campaign.")] == [
        "campaign.create",
        "campaign.patch",
    ]


@pytest.mark.parametrize(
    "change",
    [
        {"slug": "offer"},
        {"slug": "thanks"},
        {"slug": "Интенсив"},
        {"slug": "-intensive"},
        {"slug": "a"},
        {"title": "Сб"},
        {"amounts": [500000, 500000]},
        {"amounts": [5000]},
        {"amounts": [50000100]},
        {"payment_purpose": "на цель | сбора"},
        {"payment_purpose": "на цель\x00сбора"},
        {"payment_purpose": "ц" * 111},
        {"highlights": [{"value": "1", "label": "а"}] * 5},
        {"faq": [{"question": "Вопрос", "answer": ""}]},
        {"goal_amount": -1},
        {"invite_threshold": 50000100},
        {"status": "hidden"},
        {"trainer_ids": ["not-a-uuid"]},
    ],
)
async def test_invalid_campaign_is_rejected(database_app, change):
    app = database_app
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        reply = await client.post("/admin/campaigns", headers=editor, json={**BODY, **change})
    assert reply.status_code == 400, reply.text
    assert await app.state.store.read("club_campaigns", fields=("id",)) == []


async def test_campaign_address_is_unique_and_unknown_speaker_is_refused(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    other = await campaign(app, "other")
    async with client_for(app) as client:
        assert (await client.post("/admin/campaigns", headers=editor, json=BODY)).status_code == 200
        twin = await client.post("/admin/campaigns", headers=editor, json=BODY)
        moved = await client.patch("/admin/campaigns/" + other["id"], headers=editor, json={"slug": "intensive"})
        same = await client.patch("/admin/campaigns/" + other["id"], headers=editor, json={"slug": "other"})
        ghost = await client.post(
            "/admin/campaigns",
            headers=editor,
            json={**BODY, "slug": "ghost", "trainer_ids": ["11111111-1111-4111-8111-111111111111"]},
        )
        reserved = await client.patch("/admin/campaigns/" + other["id"], headers=editor, json={"slug": "consent"})
    assert (reserved.status_code, reserved.json()["error"]) == (
        400,
        "Этот адрес занят служебной страницей. Выберите другой.",
    )
    assert (twin.status_code, twin.json()["error"]) == (409, "Сбор с таким адресом уже есть.")
    assert (moved.status_code, same.status_code, ghost.status_code) == (409, 200, 400)
    slugs = await app.state.store.read("club_campaigns", fields=("slug",), sort=("slug",))
    assert [row["slug"] for row in slugs] == ["intensive", "other"]


async def test_campaign_with_donations_is_archived_not_deleted(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    kept, empty = await campaign(app, "kept"), await campaign(app, "empty")
    await donation(app, 100000, status="new", campaign_id=kept["id"])
    async with client_for(app) as client:
        refused = await client.delete("/admin/campaigns/" + kept["id"], headers=editor)
        removed = await client.delete("/admin/campaigns/" + empty["id"], headers=editor)
        archived = await client.patch("/admin/campaigns/" + kept["id"], headers=editor, json={"status": "archived"})
        page = await client.get("/campaigns/kept")
    assert (refused.status_code, refused.json()["error"]) == (
        409,
        "У сбора есть пожертвования. Вместо удаления переведите его в архив.",
    )
    assert (removed.status_code, archived.status_code, page.status_code) == (200, 200, 404)
    assert await app.state.store.read("club_campaigns", fields=("slug", "status")) == [
        {"slug": "kept", "status": "archived"}
    ]


async def test_campaigns_office_requires_staff(database_app):
    app = database_app
    item = await campaign(app, "intensive")
    async with client_for(app) as client:
        for method, path in (
            ("GET", "/admin/campaigns"),
            ("POST", "/admin/campaigns"),
            ("PATCH", "/admin/campaigns/" + item["id"]),
            ("DELETE", "/admin/campaigns/" + item["id"]),
        ):
            assert (await client.request(method, path, json=BODY)).status_code == 401
    assert await app.state.store.read("club_campaigns", fields=("slug",)) == [{"slug": "intensive"}]


async def test_intensive_template_is_saved_as_hidden_draft(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    templates = json.loads((Path(__file__).parents[3] / "data" / "campaign-templates.json").read_text(encoding="utf-8"))
    record = templates["intensive"]["record"]
    async with client_for(app) as client:
        created = await client.post("/admin/campaigns", headers=editor, json=record)
        assert created.status_code == 200, created.text
        [listed] = (await client.get("/admin/campaigns", headers=editor)).json()
        page = await client.get("/campaigns/intensive")
        home = (await client.get("/campaigns/current")).json()
    assert (listed["status"], listed["slug"], listed["invite_threshold"]) == ("draft", "intensive", 1500000)
    assert (len(listed["modules"]), len(listed["faq"]), listed["invite_body"]) == (7, 5, None)
    assert (page.status_code, home) == (404, {"campaign": None})
