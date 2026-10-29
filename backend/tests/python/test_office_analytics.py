import httpx
from test_auth_integration import fixture

PAGES = ("/dpo", "/dpo", "/news")


def counts(report):
    series = [point["count"] for points in report["series"].values() for point in points]
    paths = [row["count"] for row in report["pageviews"]["paths_top"]]
    return [report["pageviews"]["hits"], *series, *paths, *report["pulse"].values()]


async def test_analytics_and_points_are_numbers_in_every_database(database_app):
    app = database_app
    admin, _ = await fixture(app, "admin")
    _, alumni = await fixture(app)
    headers = {"authorization": "Bearer " + app.state.auth.staff_session(admin, "admin")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for path in PAGES:
            assert (await client.post("/analytics/pageview", json={"path": path})).status_code == 200
        for period in ("7d", "30d", "90d"):
            response = await client.get("/admin/analytics?range=" + period, headers=headers)
            assert response.status_code == 200
            report = response.json()
            assert report["pageviews"]["hits"] == len(PAGES)
            assert report["pageviews"]["paths_top"][0] == {"path": "/dpo", "count": 2}
            assert all(type(value) is int for value in counts(report))
        granted = await client.post(
            f"/admin/members/{alumni}/points", headers=headers, json={"delta": 12, "comment": "Проверка"}
        )
        assert granted.status_code == 200
        assert granted.json()["points"] == 12
