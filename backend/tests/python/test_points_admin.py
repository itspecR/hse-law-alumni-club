import httpx
from test_community_integration import account

PROGRAM_POINTS = 100
MENTORSHIP_POINTS = 120


async def programs(app):
    store = app.state.store
    club = await store.create("programs", {"slug": "club-points", "title": "Переговоры", "catalog": "portfolio"})
    dpo = await store.create("programs", {"slug": "dpo-points", "title": "Медиация", "catalog": "dpo"})
    return club, dpo


def achievement(me, key):
    return next(item for item in me["achievements"] if item["key"] == key)


async def test_program_points_follow_rules_once_per_program(database_app):
    app = database_app
    alumni_id, member = await account(app)
    _, admin = await account(app, "admin")
    club, dpo = await programs(app)
    path = f"/admin/members/{alumni_id}/points"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        listed = (await client.get("/admin/point-programs", headers=admin)).json()
        assert {(row["title"], row["catalog"]) for row in listed} >= {("Переговоры", "portfolio"), ("Медиация", "dpo")}
        first = await client.post(path, headers=admin, json={"reason": "program", "program_id": club["id"], "delta": 9})
        assert first.status_code == 200 and first.json()["points"] == PROGRAM_POINTS
        again = await client.post(path, headers=admin, json={"reason": "program", "program_id": club["id"]})
        assert again.status_code == 409 and "Переговоры" in again.json()["error"]
        second = await client.post(path, headers=admin, json={"reason": "program", "program_id": dpo["id"]})
        assert second.json()["points"] == 2 * PROGRAM_POINTS
        missing = await client.post(path, headers=admin, json={"reason": "program"})
        assert missing.status_code == 400
        me = (await client.get("/me", headers=member)).json()
    ledger = await app.state.store.read("points_ledger", filters={"alumni_id": {"_eq": alumni_id}}, limit=-1)
    assert {row["comment"] for row in ledger} == {"Программа: Медиация", "Программа: Переговоры"}
    assert {row["ref"] for row in ledger} == {club["id"], dpo["id"]}
    assert achievement(me, "student_again")["earned"] and achievement(me, "eternal_student")["current"] == 2


async def test_mentorship_needs_a_note_and_manual_points_are_not_zero(database_app):
    app = database_app
    alumni_id, member = await account(app)
    _, admin = await account(app, "admin")
    _, editor = await account(app, "editor")
    path = f"/admin/members/{alumni_id}/points"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.get("/admin/point-programs", headers=editor)).status_code == 403
        for body in (
            {"reason": "mentorship"},
            {"reason": "mentorship", "comment": " я "},
            {"reason": "manual", "delta": 0},
            {"reason": "manual"},
            {"reason": "event", "delta": 60},
            {"reason": "manual", "delta": 2001},
        ):
            assert (await client.post(path, headers=admin, json=body)).status_code in (400, 422), body
        mentor = await client.post(path, headers=admin, json={"reason": "mentorship", "comment": "Поток 2024"})
        assert mentor.json()["points"] == MENTORSHIP_POINTS
        manual = await client.post(path, headers=admin, json={"reason": "manual", "delta": -20})
        assert manual.json()["points"] == MENTORSHIP_POINTS - 20
        me = (await client.get("/me", headers=member)).json()
    comments = [row["comment"] for row in await app.state.store.read("points_ledger", limit=-1)]
    assert set(comments) == {"Начисление клуба", "Наставничество: Поток 2024"}
    assert achievement(me, "mentor")["earned"]
