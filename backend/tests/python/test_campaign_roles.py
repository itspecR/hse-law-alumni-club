from test_campaigns import trainer
from test_campaigns_admin import BODY, client_for
from test_community_integration import account

from club_api.modules.campaigns.service import DEFAULT_ROLE


async def test_each_person_of_a_campaign_has_a_role(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    host, coach, guest = await trainer(app, "Ведущая"), await trainer(app, "Тренер"), await trainer(app, "Гость")
    people = [host["id"], coach["id"], guest["id"]]
    roles = {host["id"]: "host", coach["id"]: "trainer"}
    async with client_for(app) as client:
        body = {**BODY, "status": "published", "trainer_ids": people, "trainer_roles": roles}
        created = await client.post("/admin/campaigns", headers=editor, json=body)
        [listed] = (await client.get("/admin/campaigns", headers=editor)).json()
        shown = (await client.get("/campaigns/intensive")).json()
        changed = await client.patch(
            "/admin/campaigns/" + listed["id"],
            headers=editor,
            json={"trainer_ids": [guest["id"]], "trainer_roles": {guest["id"]: "host"}},
        )
        [again] = (await client.get("/admin/campaigns", headers=editor)).json()
    assert created.status_code == changed.status_code == 200
    assert listed["trainer_ids"] == people
    assert listed["trainer_roles"] == {**roles, guest["id"]: DEFAULT_ROLE}
    assert [(row["fio"], row["role"]) for row in shown["speakers"]] == [
        ("Ведущая", "host"),
        ("Тренер", "trainer"),
        ("Гость", DEFAULT_ROLE),
    ]
    assert (again["trainer_ids"], again["trainer_roles"]) == ([guest["id"]], {guest["id"]: "host"})


async def test_unknown_role_is_refused(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    person = await trainer(app, "Спикер")
    async with client_for(app) as client:
        body = {**BODY, "trainer_ids": [person["id"]], "trainer_roles": {person["id"]: "boss"}}
        refused = await client.post("/admin/campaigns", headers=editor, json=body)
        listed = (await client.get("/admin/campaigns", headers=editor)).json()
    assert refused.status_code == 400 and listed == []
