import asyncio
from datetime import UTC, date, datetime, timedelta

from test_community_integration import account
from test_portfolio_applications import FORM, GROUP, PRICE, apply, client_for, mails, order, program

from club_api.modules.portfolio import dates
from club_api.modules.portfolio.dates import display_date, moscow_today

COHORT_PRICE = 3900000
__all__ = ("mails",)


def day(offset):
    return (moscow_today() + timedelta(days=offset)).isoformat()


async def cohort(app, item, offset, **extra):
    data = {"program_id": item["id"], "starts_on": day(offset)}
    return await app.state.store.create("program_cohorts", {**data, **extra})


async def details(client, item):
    return (await client.get("/portfolio/" + item["slug"])).json()


async def status(client, admin, number, app, value):
    id = (await order(app, number))["id"]
    return await client.patch("/admin/orders/" + id, headers=admin, json={"status": value})


def test_cohort_day_follows_moscow_time(monkeypatch):
    class LateEvening(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 28, 22, 30, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(dates, "datetime", LateEvening)
    assert moscow_today() == date(2026, 10, 29)


async def test_program_lists_only_open_cohorts_with_their_price_and_seats(database_app, mails):
    app = database_app
    item = await program(app)
    later = await cohort(app, item, 30, price=COHORT_PRICE, capacity=12)
    today = await cohort(app, item, 0)
    await cohort(app, item, -1)
    await cohort(app, item, 10, enrollment="nonactual")
    await cohort(app, await program(app), 5)
    async with client_for(app) as client:
        cohorts = (await details(client, item))["cohorts"]
    assert [row["id"] for row in cohorts] == [today["id"], later["id"]]
    assert [(row["price"], row["capacity"], row["seats_left"]) for row in cohorts] == [
        (PRICE, None, None),
        (COHORT_PRICE, 12, 12),
    ]


async def test_application_goes_to_the_chosen_open_cohort_at_its_price(database_app, mails):
    app = database_app
    item = await program(app)
    chosen = await cohort(app, item, 14, price=COHORT_PRICE)
    past = await cohort(app, item, -1)
    closed = await cohort(app, item, 20, enrollment="nonactual")
    foreign = await cohort(app, await program(app), 14)
    async with client_for(app) as client:
        missing = await apply(client, item["slug"], FORM)
        refused = [
            await apply(client, item["slug"], {**FORM, "cohort_id": row["id"]}) for row in (past, closed, foreign)
        ]
        response = await apply(client, item["slug"], {**FORM, "cohort_id": chosen["id"].upper()})
    assert missing.status_code == 400 and "Выберите поток" in missing.json()["error"]
    assert all(row.status_code == 400 and "Набор в этот поток закрыт" in row.json()["error"] for row in refused)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert (receipt["starts_on"], receipt["total_estimate"]) == (chosen["starts_on"], COHORT_PRICE)
    saved = await order(app, receipt["number"])
    assert (saved["cohort_id"], saved["items_json"][0]["price"]) == (chosen["id"], COHORT_PRICE)
    assert f"поток с {display_date(chosen['starts_on'])}" in mails[-1]["text"]
    assert len(await app.state.store.read("orders", fields=("id",))) == 1


async def test_program_without_open_cohorts_takes_applications_as_before(database_app, mails):
    app = database_app
    item = await program(app)
    past = await cohort(app, item, -3, price=COHORT_PRICE)
    async with client_for(app) as client:
        assert (await details(client, item))["cohorts"] == []
        stale = await apply(client, item["slug"], {**FORM, "cohort_id": past["id"]})
        response = await apply(client, item["slug"], FORM)
    assert stale.status_code == 400
    receipt = response.json()
    assert (receipt["starts_on"], receipt["total_estimate"]) == (None, PRICE)
    assert (await order(app, receipt["number"]))["cohort_id"] is None
    assert "поток с" not in mails[-1]["text"]


async def test_quote_uses_the_cohort_price(database_app, mails):
    app = database_app
    item = await program(app)
    chosen = await cohort(app, item, 7, price=COHORT_PRICE)
    async with client_for(app) as client:
        url = f"/portfolio/{item['slug']}/quote"
        missing = await client.post(url, json={"applicant": "self"})
        priced = await client.post(url, json={"applicant": "self", "cohort_id": chosen["id"]})
    assert missing.status_code == 400 and "Выберите поток" in missing.json()["error"]
    assert priced.json()["total"] == COHORT_PRICE


async def test_cohort_seats_are_counted_apart_from_the_program(database_app, mails):
    app = database_app
    item = await program(app, capacity=2)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        general = (await apply(client, item["slug"], FORM)).json()["number"]
        limited = await cohort(app, item, 10, capacity=2)
        free = await cohort(app, item, 40)
        pair = (await apply(client, item["slug"], {**GROUP, "participants": 2, "cohort_id": limited["id"]})).json()
        extra = (await apply(client, item["slug"], {**FORM, "cohort_id": limited["id"]})).json()
        other = (await apply(client, item["slug"], {**FORM, "cohort_id": free["id"]})).json()
        assert (await status(client, admin, general, app, "confirmed")).status_code == 200
        assert (await status(client, admin, pair["number"], app, "confirmed")).status_code == 200
        refused = await status(client, admin, extra["number"], app, "confirmed")
        assert refused.status_code == 409 and "подтверждено 2 из 2" in refused.json()["error"]
        assert (await status(client, admin, other["number"], app, "confirmed")).status_code == 200
        shown = await details(client, item)
    assert shown["seats_left"] == 1
    assert [row["seats_left"] for row in shown["cohorts"]] == [0, None]


async def test_last_cohort_seat_is_confirmed_once_under_concurrency(database_app, mails):
    app = database_app
    item = await program(app)
    limited = await cohort(app, item, 10, capacity=1)
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        numbers = [
            (await apply(client, item["slug"], {**FORM, "cohort_id": limited["id"]})).json()["number"] for _ in range(2)
        ]
        results = await asyncio.gather(*(status(client, admin, number, app, "confirmed") for number in numbers))
    assert sorted(response.status_code for response in results) == [200, 409]


async def test_office_manages_cohorts_of_club_programs(database_app, mails):
    app = database_app
    item, other, dpo = await program(app), await program(app), await program(app, catalog="dpo")
    _, admin = await account(app, "admin")
    _, member = await account(app)
    async with client_for(app) as client:
        body = {"program_id": item["id"], "starts_on": day(20), "price": COHORT_PRICE, "capacity": 15}
        assert (await client.post("/admin/cohorts", headers=member, json=body)).status_code in (401, 403)
        created = await client.post("/admin/cohorts", headers=admin, json=body)
        assert created.status_code == 200, created.text
        id = created.json()["id"]
        wrong = [
            await client.post("/admin/cohorts", headers=admin, json={**body, "program_id": dpo["id"]}),
            await client.post("/admin/cohorts", headers=admin, json={**body, "starts_on": "31.12.2026"}),
            await client.post("/admin/cohorts", headers=admin, json={**body, "capacity": 0}),
        ]
        assert [response.status_code for response in wrong] == [400, 400, 400]
        assert (await client.patch("/admin/cohorts/" + id, headers=admin, json={"price": None})).status_code == 200
        missing = await client.patch(f"/admin/cohorts/{item['id']}", headers=admin, json={"price": 1})
        assert missing.status_code == 404 and missing.json()["error"] == "Поток не найден."
        applied = (await apply(client, item["slug"], {**FORM, "cohort_id": id})).json()
        assert applied["total_estimate"] == PRICE
        await status(client, admin, applied["number"], app, "confirmed")
        listed = (await client.get("/admin/cohorts", headers=admin)).json()
        moved = await client.patch("/admin/cohorts/" + id, headers=admin, json={"program_id": other["id"]})
        deleted = await client.delete("/admin/cohorts/" + id, headers=admin)
        spare = (await client.post("/admin/cohorts", headers=admin, json={**body, "starts_on": day(-1)})).json()["id"]
        relocated = await client.patch("/admin/cohorts/" + spare, headers=admin, json={"program_id": other["id"]})
        removed = await client.delete("/admin/cohorts/" + spare, headers=admin)
    assert [(row["id"], row["taken"], row["open"]) for row in listed] == [(id, 1, True)]
    assert moved.status_code == 409 and deleted.status_code == 409
    assert "закройте набор" in deleted.json()["error"]
    assert relocated.status_code == 200 and removed.status_code == 200
    assert [row["id"] for row in await app.state.store.read("program_cohorts", fields=("id",))] == [id]


async def test_catalog_cards_carry_open_cohort_dates_and_prices(database_app, mails):
    app = database_app
    item, plain = await program(app), await program(app)
    later = await cohort(app, item, 21)
    sooner = await cohort(app, item, 3, price=COHORT_PRICE)
    await cohort(app, item, -2)
    async with client_for(app) as client:
        cards = {row["id"]: row for row in (await client.get("/portfolio")).json()}
    assert cards[item["id"]]["cohorts"] == [
        {"starts_on": sooner["starts_on"], "price": COHORT_PRICE},
        {"starts_on": later["starts_on"], "price": PRICE},
    ]
    assert cards[plain["id"]]["cohorts"] == []


async def test_cabinet_shows_the_cohort_start(database_app, mails):
    app = database_app
    item = await program(app)
    chosen = await cohort(app, item, 9)
    _, member = await account(app)
    async with client_for(app) as client:
        await apply(client, item["slug"], {**FORM, "cohort_id": chosen["id"]}, member)
        await app.state.store.update("program_cohorts", {"enrollment": "nonactual"}, id=chosen["id"])
        await apply(client, item["slug"], FORM, member)
        rows = (await client.get("/me/orders", headers=member)).json()
    assert sorted(row["starts_on"] or "" for row in rows) == ["", chosen["starts_on"]]
