from uuid import uuid4

import httpx
import pytest
from club_ops.alumni import DEFAULT_FIO, create_alumni
from club_ops.bootstrap import OperatorError

ADDRESS = "Graduate.Test@Example.test"
PASSWORD = "synthetic-alumni-password"


async def alumni_role(app):
    await app.state.database.execute("INSERT INTO directus_roles(id,name) VALUES(%s,'alumni')", (str(uuid4()),))


async def users(app):
    return (await app.state.database.rows("SELECT count(*) AS total FROM directus_users"))[0]["total"]


async def test_operator_alumni_signs_in_to_verified_cabinet(database_app):
    app = database_app
    await alumni_role(app)
    async with app.state.database.connection() as connection:
        assert await create_alumni(connection, address=ADDRESS, fio="", password=PASSWORD) == ADDRESS.lower()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        login = await client.post("/auth/login", json={"email": ADDRESS.lower(), "password": PASSWORD})
        assert login.status_code == 200, login.text
        me = await client.get("/me", headers={"Authorization": "Bearer " + login.json()["token"]})
    assert me.status_code == 200
    alumni = me.json()["alumni"]
    assert (alumni["fio"], alumni["verification_status"]) == (DEFAULT_FIO, "verified")


async def test_operator_alumni_keeps_existing_account(database_app):
    app = database_app
    await alumni_role(app)
    async with app.state.database.connection() as connection:
        await create_alumni(connection, address=ADDRESS, fio="Первый Выпускник", password=PASSWORD)
        before = await users(app)
        with pytest.raises(OperatorError):
            await create_alumni(connection, address=ADDRESS.lower(), fio="Второй Выпускник", password=PASSWORD)
    assert await users(app) == before


@pytest.mark.parametrize(
    "address,fio,password",
    [
        ("not-an-email", "", PASSWORD),
        (ADDRESS, "Я", PASSWORD),
        (ADDRESS, "", "short"),
        (ADDRESS, "", "x" * 101),
        (ADDRESS, "", "synthetic\npassword"),
    ],
)
async def test_operator_alumni_rejects_bad_input(database_app, address, fio, password):
    app = database_app
    await alumni_role(app)
    async with app.state.database.connection() as connection:
        with pytest.raises(OperatorError):
            await create_alumni(connection, address=address, fio=fio, password=password)
    assert await users(app) == 0
