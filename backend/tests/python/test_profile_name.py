import unicodedata

import httpx
from test_community_integration import account

from club_api.modules.members.profile import FIO_LOCKED

FIO = "Вымышленный участник"


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def stored_fio(app, alumni_id):
    rows = await app.state.database.rows("SELECT fio FROM alumni WHERE id=%s", (alumni_id,))
    return rows[0]["fio"]


async def test_verified_alumni_cannot_change_own_name(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    async with client_for(app) as client:
        changed = await client.patch("/me/profile", headers=headers, json={"fio": "Другое Имя"})
        same = await client.patch("/me/profile", headers=headers, json={"fio": "  Вымышленный   участник "})
        contacts = await client.patch("/me/profile", headers=headers, json={"contacts": {"phone": "+70000000000"}})
    assert (changed.status_code, changed.json()["error"]) == (409, FIO_LOCKED)
    assert same.status_code == contacts.status_code == 200
    assert await stored_fio(app, alumni_id) == FIO


async def test_name_check_survives_empty_stored_name(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    await app.state.database.execute("UPDATE alumni SET fio=NULL WHERE id=%s", (alumni_id,))
    async with client_for(app) as client:
        changed = await client.patch("/me/profile", headers=headers, json={"fio": "Другое Имя"})
    assert (changed.status_code, changed.json()["error"]) == (409, FIO_LOCKED)
    assert await stored_fio(app, alumni_id) is None


async def test_name_check_compares_normalized_forms(database_app):
    app = database_app
    alumni_id, headers = await account(app)
    decomposed = unicodedata.normalize("NFD", "Вымышленный Йорик")
    await app.state.database.execute("UPDATE alumni SET fio=%s WHERE id=%s", (decomposed, alumni_id))
    async with client_for(app) as client:
        same = await client.patch("/me/profile", headers=headers, json={"fio": "Вымышленный Йорик"})
    assert same.status_code == 200
    assert await stored_fio(app, alumni_id) == decomposed
