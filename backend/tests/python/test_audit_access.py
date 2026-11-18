import httpx
from test_community_integration import account


async def test_only_full_admin_reads_audit_log(database_app):
    app = database_app
    _, editor = await account(app, "editor")
    _, admin = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        forbidden = await client.get("/admin/audit", headers=editor)
        allowed = await client.get("/admin/audit", headers=admin)
        anonymous = await client.get("/admin/audit")
    assert (forbidden.status_code, allowed.status_code, anonymous.status_code) == (403, 200, 401)


async def test_audit_log_pages_and_filters_by_event_and_staff(database_app):
    app = database_app
    _, admin = await account(app, "admin")
    staff = "admin:" + app.state.auth.decode(admin["Authorization"].removeprefix("Bearer "), admin=True)["sub"]
    store = app.state.store
    for index in range(3):
        await store.create("audit_log", {"event": "news.create", "actor": staff, "subject": f"news-{index}"})
    await store.create("audit_log", {"event": "support.read", "actor": staff, "subject": "ticket"})
    await store.create("audit_log", {"event": "login.fail", "actor": "email:x@example.test", "subject": None})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = (await client.get("/admin/audit?limit=2&event=news.create", headers=admin)).json()
        second = (await client.get("/admin/audit?limit=2&page=2&event=news.create", headers=admin)).json()
        hidden = (await client.get("/admin/audit?actor=" + staff, headers=admin)).json()
        reads = (await client.get("/admin/audit?reads=1&actor=" + staff, headers=admin)).json()
        wrong = await client.get("/admin/audit?event=DROP%20TABLE", headers=admin)
    assert (first["total"], len(first["items"]), len(second["items"])) == (3, 2, 1)
    assert {row["event"] for row in hidden["items"]} == {"news.create"} and reads["total"] == hidden["total"] + 1
    assert {"actor": staff, "label": staff.removeprefix("admin:") + "@example.test"} in first["staff"]
    assert {"news.create", "support.read", "login.fail"} <= set(first["events"])
    assert wrong.status_code == 400
