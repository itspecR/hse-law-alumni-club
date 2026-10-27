from uuid import uuid4

import httpx
import pytest
from test_community_integration import account
from test_portfolio_applications import FORM, GROUP, PRICE, apply, mails, order, program
from test_portfolio_promo import create_promo, promo_row, with_code

from club_api.domain import rubles

__all__ = ("mails",)


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


async def set_discount(client, headers, id, body):
    return await client.patch(f"/admin/orders/{id}/discount", headers=headers, json=body)


async def submitted(app, client, body, headers=None, **extra):
    item = await program(app, **extra)
    receipt = (await apply(client, item["slug"], body, headers)).json()
    return receipt, await order(app, receipt["number"])


def test_rubles_keep_large_sums_readable():
    assert rubles(135000000) == "1 350 000 ₽"
    assert rubles(3825050) == "38 250,50 ₽"
    assert rubles(None) == "0 ₽"


async def test_application_mail_shows_large_sum_in_rubles(database_app, mails):
    app = database_app
    async with client_for(app) as client:
        await submitted(app, client, GROUP, price=45000000)
    assert "Сумма (справочно): 1 350 000 ₽." in mails[0]["text"]


async def test_office_discount_replaces_member_discount_and_can_be_reset(database_app, mails):
    app = database_app
    _, editor = await account(app, "editor")
    _, member = await account(app)
    async with client_for(app) as client:
        receipt, row = await submitted(app, client, FORM, member)
        response = await set_discount(client, editor, row["id"], {"discount": 30, "reason": " Договор с ООО «Пример» "})
        listed = (await client.get("/admin/orders", headers=editor)).json()["items"][0]
        own = (await client.get("/me/orders", headers=member)).json()[0]
        changed = await order(app, receipt["number"])
        reset = await set_discount(client, editor, row["id"], {"discount": None})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True,
        "discount": 30,
        "office_discount": 30,
        "office_reason": "Договор с ООО «Пример»",
        "total_estimate": PRICE * 70 // 100,
    }
    assert (changed["member_discount"], changed["discount_source"]) == (receipt["member_discount"], "member")
    assert (listed["office_discount"], listed["office_reason"], listed["total_estimate"]) == (
        30,
        "Договор с ООО «Пример»",
        PRICE * 70 // 100,
    )
    assert own["office_discount"] == 30 and "office_reason" not in own and own["discount_source"] == "member"
    assert reset.json()["total_estimate"] == receipt["total_estimate"] and reset.json()["office_reason"] is None
    assert [mail["to"] for mail in mails[1:]] == ["applicant@example.test", "applicant@example.test"]
    assert "скидка учебного офиса 30%" in mails[1]["text"] and rubles(PRICE * 70 // 100) in mails[1]["text"]
    assert f"отменена, действует скидка {receipt['member_discount']}%" in mails[2]["text"]
    assert "ООО «Пример»" not in mails[1]["text"]
    events = await app.state.store.read("audit_log", filters={"event": {"_eq": "order.discount"}})
    assert len(events) == 2 and events[0]["subject"] == "order:" + receipt["number"]


async def test_office_discount_replaces_promo_and_keeps_its_use(database_app, mails):
    app = database_app
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        promo = await create_promo(client, admin)
        receipt, row = await submitted(app, client, with_code(GROUP))
        response = await set_discount(client, admin, row["id"], {"discount": 5, "reason": "Корпоративный договор"})
        export = (await client.get("/admin/orders/export.csv", headers=admin)).text
        used = (await promo_row(client, admin, promo))["used"]
    assert receipt["discount_source"] == "promo" and response.json()["total_estimate"] == 3 * PRICE * 95 // 100
    assert used == 1 and (await order(app, receipt["number"]))["promo_code"] == "SPRING-15"
    line = next(line for line in export.splitlines() if receipt["number"] in line)
    assert '"5"' in line and '"Корпоративный договор"' in line


async def test_same_discount_changes_reason_without_mail(database_app, mails):
    app = database_app
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        _, row = await submitted(app, client, GROUP)
        first = await set_discount(client, admin, row["id"], {"discount": 0, "reason": "Без скидки по договору"})
        second = await set_discount(client, admin, row["id"], {"discount": 10, "reason": "Договор"})
        again = await set_discount(client, admin, row["id"], {"discount": 10, "reason": "Договор № 7"})
    assert first.status_code == second.status_code == again.status_code == 200
    assert len(mails) == 2 and "скидка учебного офиса 10%" in mails[1]["text"]
    assert again.json()["office_reason"] == "Договор № 7"


@pytest.mark.parametrize(
    "body",
    [
        {"discount": 101, "reason": "Договор"},
        {"discount": -1, "reason": "Договор"},
        {"discount": 10},
        {"discount": 10, "reason": "  "},
        {"discount": "10", "reason": "Договор"},
        {"reason": "Договор"},
        {"discount": 10, "reason": "Д" * 301},
    ],
)
async def test_office_discount_rejects_invalid_body(database_app, mails, body):
    app = database_app
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        receipt, row = await submitted(app, client, GROUP)
        response = await set_discount(client, admin, row["id"], body)
    assert response.status_code == 400
    assert (await order(app, receipt["number"]))["office_discount"] is None and len(mails) == 1


@pytest.mark.parametrize("status", ["done", "canceled"])
async def test_closed_application_keeps_its_sum(database_app, mails, status):
    app = database_app
    _, admin = await account(app, "admin")
    async with client_for(app) as client:
        receipt, row = await submitted(app, client, GROUP)
        await client.patch("/admin/orders/" + row["id"], headers=admin, json={"status": status})
        response = await set_discount(client, admin, row["id"], {"discount": 10, "reason": "Договор"})
    assert response.status_code == 409 and "скидку изменить нельзя" in response.json()["error"]
    assert (await order(app, receipt["number"]))["total_estimate"] == 3 * PRICE


async def test_office_discount_is_only_for_club_programs_and_staff(database_app):
    app = database_app
    _, admin = await account(app, "admin")
    _, member = await account(app)
    dpo = await app.state.store.create(
        "orders", {"number": "ALU-TEST-DPO", "type": "dpo", "items_json": [], "status": "new"}
    )
    body = {"discount": 10, "reason": "Договор"}
    async with client_for(app) as client:
        other = await set_discount(client, admin, dpo["id"], body)
        missing = await set_discount(client, admin, str(uuid4()), body)
        malformed = await set_discount(client, admin, "not-an-id", body)
        guest = await set_discount(client, {}, dpo["id"], body)
        alumni = await set_discount(client, member, dpo["id"], body)
    assert other.status_code == 400 and "программу клуба" in other.json()["error"]
    assert (missing.status_code, malformed.status_code, guest.status_code, alumni.status_code) == (404, 400, 401, 401)
