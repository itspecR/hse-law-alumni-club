from uuid import uuid4

import httpx
import pytest
from test_auth_integration import fixture


@pytest.mark.parametrize(
    ("verified", "personal", "discount"), [(False, 0, 0), (True, 0, 5), (True, 15, 15), (False, 25, 0)]
)
async def test_cart_uses_current_catalog_prices_and_server_discount(database_app, verified, personal, discount):
    app = database_app
    user, alumni = await fixture(app)
    await app.state.store.update(
        "alumni",
        {
            "verification_status": "verified" if verified else "pending",
            "points_cached": 0,
            "personal_discount": personal,
        },
        id=alumni,
    )
    course = await app.state.store.create(
        "programs", {"slug": str(uuid4()), "title": "Проверка цены курса", "price": 100000, "status": "published"}
    )
    product = await app.state.store.create(
        "products",
        {"slug": str(uuid4()), "title": "Проверка цены мерча", "price": 50000, "stock": 2, "status": "published"},
    )
    headers = {"x-cart-session": str(uuid4()), "authorization": "Bearer " + app.state.auth.session(alumni, user)}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for kind, row in [("dpo", course), ("merch", product)]:
            assert (
                await client.post("/cart", headers=headers, json={"type": kind, "ref_id": row["slug"]})
            ).status_code == 200
        await app.state.store.update("programs", {"price": 200000}, id=course["id"])
        estimate = (await client.get("/cart", headers=headers)).json()
        assert estimate["member_discount"] == discount
        assert estimate["subtotal"] == 250000
        assert next(item for item in estimate["items"] if item["type"] == "dpo")["price"] == 200000
        assert estimate["estimated_total"] == 200000 * (100 - discount) // 100 + 50000
        guest = (await client.get("/cart", headers={"x-cart-session": headers["x-cart-session"]})).json()
        assert guest["member_discount"] == 0 and guest["estimated_total"] == 250000


async def test_site_bot_keeps_private_programs_out_of_answers(database_app):
    app = database_app
    for status, price in [("published", 5000000), ("draft", 1)]:
        await app.state.store.create(
            "programs",
            {
                "slug": status,
                "title": "Программа права " + status,
                "price": price,
                "format": "online",
                "status": status,
            },
        )
    await app.state.store.create(
        "programs",
        {
            "slug": "club",
            "title": "Программа права клуба",
            "price": 5000000,
            "format": "online",
            "status": "published",
            "catalog": "portfolio",
        },
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        joining = await client.post("/support/ask", json={"question": "Как вступить в клуб"})
        assert joining.status_code == 200 and joining.json()["anchor"] == "/join"
        format_answer = await client.post("/support/ask", json={"question": "онлайн"})
        assert format_answer.status_code == 200 and format_answer.json()["anchor"] == "/dpo"
        selection = await client.post("/support/ask", json={"question": "программы права"})
        assert selection.status_code == 200
        programs = selection.json()["programs"]
        assert programs and all(program["url"] == "/dpo/published" for program in programs)
        unknown = await client.post("/support/ask", json={"question": "Совершенно несуществующий запрос"})
        assert unknown.status_code == 200
        assert unknown.json()["anchor"] == "/support"
        records = await app.state.database.rows("SELECT kind,gap_id,channel FROM club_faq_events")
        assert records and all("несуществующий" not in str(row) for row in records)
        assert (await client.post("/support/ask", json={"question": "x"})).status_code == 400
        assert (await client.post("/support/ask", json={"question": "x" * 501})).status_code == 400


@pytest.mark.parametrize("role", ["alumni", "editor", "admin"])
async def test_server_validates_office_session_role(database_app, role):
    app = database_app
    user, alumni = await fixture(app, role)
    token = app.state.auth.session(alumni, user) if role == "alumni" else app.state.auth.staff_session(user, role)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/auth/admin-session", headers={"authorization": "Bearer " + token})
        assert response.status_code == (401 if role == "alumni" else 200)
        if role != "alumni":
            assert response.json() == {"role": role}
