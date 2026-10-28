import asyncio

from test_community_integration import account
from test_portfolio_applications import FORM, GROUP, PRICE, apply, client_for, mails, order, program
from test_portfolio_cohorts import COHORT_PRICE, cohort

from club_api.domain import rubles
from club_api.modules.portfolio.dates import display_date
from club_api.modules.portfolio.orders import CONTACT_LINE

__all__ = ("mails",)


async def move(client, headers, id, cohort_id):
    return await client.patch(f"/admin/orders/{id}/cohort", headers=headers, json={"cohort_id": cohort_id})


async def submitted(app, client, item, body):
    receipt = (await apply(client, item["slug"], body)).json()
    return await order(app, receipt["number"])


async def set_status(client, headers, id, status):
    return await client.patch("/admin/orders/" + id, headers=headers, json={"status": status})


async def test_office_moves_application_and_recalculates_total(database_app, mails):
    app = database_app
    item = await program(app)
    first, second = await cohort(app, item, 10), await cohort(app, item, 40, price=COHORT_PRICE)
    _, editor = await account(app, "editor")
    async with client_for(app) as client:
        row = await submitted(app, client, item, {**GROUP, "cohort_id": first["id"]})
        await client.patch(
            f"/admin/orders/{row['id']}/discount", headers=editor, json={"discount": 10, "reason": "Договор"}
        )
        response = await move(client, editor, row["id"], second["id"])
        listed = (await client.get("/admin/orders", headers=editor)).json()["items"][0]
    assert response.status_code == 200, response.text
    total = 3 * COHORT_PRICE * 90 // 100
    assert response.json() == {
        "ok": True,
        "cohort_id": second["id"],
        "starts_on": second["starts_on"],
        "subtotal": 3 * COHORT_PRICE,
        "total_estimate": total,
    }
    saved = await order(app, row["number"])
    assert (saved["cohort_id"], saved["items_json"][0]["price"], saved["total_estimate"]) == (
        second["id"],
        COHORT_PRICE,
        total,
    )
    assert (listed["cohort_id"], listed["starts_on"], listed["total_estimate"]) == (
        second["id"],
        second["starts_on"],
        total,
    )
    assert f"со стартом {display_date(second['starts_on'])}" in mails[-1]["text"]
    assert f"Предварительная сумма {rubles(total)}." in mails[-1]["text"] and mails[-1]["text"].endswith(CONTACT_LINE)
    assert (
        mails[-1]["subject"]
        == f"Заявка {row['number']} перенесена в другой поток - Клуб выпускников факультета права Вышки"
    )
    events = await app.state.store.read("audit_log", filters={"event": {"_eq": "order.cohort"}})
    assert [event["subject"] for event in events] == ["order:" + row["number"]]


async def test_application_without_cohort_can_join_one_at_its_price(database_app, mails):
    app = database_app
    item = await program(app)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        row = await submitted(app, client, item, FORM)
        later = await cohort(app, item, 30, price=COHORT_PRICE)
        response = await move(client, admin, row["id"], later["id"])
    assert response.status_code == 200 and response.json()["total_estimate"] == COHORT_PRICE
    assert row["total_estimate"] == PRICE


async def test_confirmed_application_needs_a_free_seat_in_the_new_cohort(database_app, mails):
    app = database_app
    item = await program(app)
    first, full = await cohort(app, item, 10), await cohort(app, item, 20, capacity=1)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        taken = await submitted(app, client, item, {**FORM, "cohort_id": full["id"]})
        confirmed = await submitted(app, client, item, {**FORM, "cohort_id": first["id"]})
        fresh = await submitted(app, client, item, {**FORM, "cohort_id": first["id"]})
        await set_status(client, admin, taken["id"], "confirmed")
        await set_status(client, admin, confirmed["id"], "confirmed")
        refused = await move(client, admin, confirmed["id"], full["id"])
        accepted = await move(client, admin, fresh["id"], full["id"])
    assert refused.status_code == 409 and "Мест не осталось: подтверждено 1 из 1" in refused.json()["error"]
    assert accepted.status_code == 200
    assert (await order(app, confirmed["number"]))["cohort_id"] == first["id"]


async def test_move_is_limited_to_open_applications_and_own_program(database_app, mails):
    app = database_app
    item, other = await program(app), await program(app)
    first, second, foreign = await cohort(app, item, 10), await cohort(app, item, 20), await cohort(app, other, 10)
    _, admin = await account(app, "admin")
    _, member = await account(app)
    async with client_for(app) as client:
        row = await submitted(app, client, item, {**FORM, "cohort_id": first["id"]})
        closed = await submitted(app, client, item, {**FORM, "cohort_id": first["id"]})
        await set_status(client, admin, closed["id"], "canceled")
        responses = {
            "member": await move(client, member, row["id"], second["id"]),
            "foreign": await move(client, admin, row["id"], foreign["id"]),
            "same": await move(client, admin, row["id"], first["id"]),
            "closed": await move(client, admin, closed["id"], second["id"]),
            "missing": await move(client, admin, item["id"], second["id"]),
            "malformed": await move(client, admin, row["id"], "not-an-id"),
        }
    assert responses["member"].status_code in (401, 403)
    assert (
        responses["foreign"].status_code == 400 and "не найден в этой программе" in responses["foreign"].json()["error"]
    )
    assert responses["same"].status_code == 409 and responses["same"].json()["error"] == "Заявка уже в этом потоке."
    assert responses["closed"].status_code == 409 and "поток изменить нельзя" in responses["closed"].json()["error"]
    assert responses["missing"].status_code == 404 and responses["malformed"].status_code == 400
    assert (await order(app, row["number"]))["cohort_id"] == first["id"]


async def test_export_names_the_cohort_start(database_app, mails):
    app = database_app
    item = await program(app)
    chosen = await cohort(app, item, 12)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        row = await submitted(app, client, item, {**FORM, "cohort_id": chosen["id"]})
        export = (await client.get("/admin/orders/export.csv", headers=admin)).text
    header, line = export.splitlines()[0], next(line for line in export.splitlines() if row["number"] in line)
    assert header.endswith('"Старт потока"') and line.endswith(f'"{display_date(chosen["starts_on"])}"')


async def test_move_and_confirmation_share_the_last_seat(database_app, mails):
    app = database_app
    item = await program(app)
    first, last = await cohort(app, item, 10), await cohort(app, item, 20, capacity=1)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        moving = await submitted(app, client, item, {**FORM, "cohort_id": first["id"]})
        waiting = await submitted(app, client, item, {**FORM, "cohort_id": last["id"]})
        await set_status(client, admin, moving["id"], "confirmed")
        results = await asyncio.gather(
            move(client, admin, moving["id"], last["id"]), set_status(client, admin, waiting["id"], "confirmed")
        )
    assert sorted(response.status_code for response in results) == [200, 409]
    seated = await app.state.store.read(
        "orders", filters={"cohort_id": {"_eq": last["id"]}, "status": {"_eq": "confirmed"}}, fields=("id",)
    )
    assert len(seated) == 1
