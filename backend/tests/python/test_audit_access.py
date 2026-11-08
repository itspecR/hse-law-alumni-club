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
