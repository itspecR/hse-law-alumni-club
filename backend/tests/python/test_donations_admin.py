import csv
import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from test_community_integration import account

DONATIONS = (
    ("DON-2026-000003", "succeeded", 300000, "Анна Вымышленная", "anna@example.test", 1, "synthetic-pay-3"),
    ("DON-2026-000002", "review", 150000, None, "boris@example.test", 2, "synthetic-pay-2"),
    ("DON-2026-000001", "succeeded", 100000, None, "-", 40, "synthetic-pay-1"),
)


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def seed(app):
    now = datetime.now(UTC)
    for number, status, amount, name, email, days, payment in DONATIONS:
        created = now - timedelta(days=days)
        await app.state.database.execute(
            "INSERT INTO club_donations(id,number,amount,email,name,status,payment_id,paid_at,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                str(uuid4()),
                number,
                amount,
                email,
                name,
                status,
                payment,
                created if status == "succeeded" else None,
                created,
            ),
        )


async def listing(http, headers, query=""):
    reply = await http.get("/admin/donations" + query, headers=headers)
    assert reply.status_code == 200, reply.text
    return reply.json()


async def test_full_admin_sees_donations_and_paid_total_for_period(database_app):
    app = database_app
    await seed(app)
    _, headers = await account(app, "admin")
    async with client(app) as http:
        month = await listing(http, headers)
        everything = await listing(http, headers, "?range=all")
        review = await listing(http, headers, "?range=all&status=review")
        found = await listing(http, headers, "?range=all&q=ANNA")
        wrong = await http.get("/admin/donations?range=week", headers=headers)
    assert [row["number"] for row in month["items"]] == ["DON-2026-000003", "DON-2026-000002"]
    assert (month["range"], month["total"], month["summary"]) == ("30d", 2, {"count": 1, "amount": 300000})
    assert (everything["total"], everything["summary"]) == (3, {"count": 2, "amount": 400000})
    assert [row["number"] for row in review["items"]] == ["DON-2026-000002"]
    assert [row["number"] for row in found["items"]] == ["DON-2026-000003"]
    assert month["statuses"]["review"] == "На проверке" and month["periods"]["all"] == "Все время"
    assert wrong.status_code == 400


async def test_editor_cannot_open_or_export_donations(database_app):
    app = database_app
    await seed(app)
    _, headers = await account(app, "editor")
    async with client(app) as http:
        listed = await http.get("/admin/donations", headers=headers)
        exported = await http.get("/admin/donations/export.csv", headers=headers)
        anonymous = await http.get("/admin/donations")
    assert (listed.status_code, exported.status_code) == (403, 403)
    assert anonymous.status_code == 401


async def test_donations_export_follows_filters_and_is_audited(database_app):
    app = database_app
    await seed(app)
    _, headers = await account(app, "admin")
    async with client(app) as http:
        reply = await http.get("/admin/donations/export.csv?range=30d", headers=headers)
    assert reply.status_code == 200 and reply.headers["content-type"].startswith("text/csv")
    header, *rows = list(csv.reader(io.StringIO(reply.text.lstrip("\ufeff")), delimiter=";"))
    assert header[:4] == ["Номер", "Дата", "Сумма, ₽", "Статус"]
    assert [row[0] for row in rows] == ["DON-2026-000003", "DON-2026-000002"]
    assert rows[0][2:7] == ["3000,00", "Оплачено", "Анна Вымышленная", "anna@example.test", ""]
    assert rows[1][3] == "На проверке" and rows[1][8] == ""
    assert header[9] == "Способ" and rows[0][9] == "Картой"
    [event] = await app.state.database.rows("SELECT detail FROM audit_log WHERE event='donations.export'")
    detail = event["detail"]
    assert (json.loads(detail) if isinstance(detail, str) else detail) == {"count": 2, "range": "30d"}
