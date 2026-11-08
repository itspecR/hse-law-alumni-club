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


async def stored_user_names(app, alumni_id):
    rows = await app.state.database.rows(
        "SELECT u.first_name,u.last_name FROM directus_users u JOIN alumni a ON a.user_id=u.id WHERE a.id=%s",
        (alumni_id,),
    )
    return rows[0]["first_name"], rows[0]["last_name"]


async def test_full_admin_corrects_verified_alumni_name(database_app):
    app = database_app
    alumni_id, _ = await account(app)
    _, editor = await account(app, "editor")
    _, admin = await account(app, "admin")
    path = "/admin/members/" + alumni_id
    async with client_for(app) as client:
        denied = await client.patch(path, headers=editor, json={"fio": "Исправленный Участник"})
        invalid = await client.patch(path, headers=admin, json={"fio": "Участник 2"})
        fixed = await client.patch(path, headers=admin, json={"fio": "  Исправленный   Участник "})
    assert (denied.status_code, invalid.status_code, fixed.status_code) == (403, 400, 200)
    assert await stored_fio(app, alumni_id) == "Исправленный Участник"
    assert await stored_user_names(app, alumni_id) == ("Исправленный", "Участник")
    rows = await app.state.database.rows(
        "SELECT detail FROM audit_log WHERE event='member.patch' AND subject=%s", ("alumni:" + alumni_id,)
    )
    assert len(rows) == 1 and "Исправленный" not in str(rows[0]["detail"]) and "fio_changed" in str(rows[0]["detail"])


async def test_long_name_fits_user_record(database_app):
    app = database_app
    alumni_id, _ = await account(app)
    _, admin = await account(app, "admin")
    long_name = "Анна " + "-".join(["Длиннофамильная"] * 6)
    async with client_for(app) as client:
        fixed = await client.patch("/admin/members/" + alumni_id, headers=admin, json={"fio": long_name})
    first, last = await stored_user_names(app, alumni_id)
    assert fixed.status_code == 200 and await stored_fio(app, alumni_id) == long_name
    assert (first, last) == ("Анна", long_name.split(" ", 1)[1][:50])
