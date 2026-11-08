from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from test_community_integration import account

from club_api.core.errors import ApiError
from club_api.db.store import Store, check_fields, check_filters, condition, model_for

PAYLOADS = (
    "' OR '1'='1",
    "x'; DROP TABLE news; --",
    "' UNION SELECT password FROM directus_users --",
    "' OR pg_sleep(1) IS NULL --",
    "' OR SLEEP(1)=0 #",
    "x\\' OR 1=1 --%_",
)


class NoDatabaseAccess:
    def connection(self):
        raise AssertionError("Недопустимый запрос дошёл до базы")

    def transaction(self):
        raise AssertionError("Недопустимый запрос дошёл до транзакции")

    async def rows(self, *args):
        raise AssertionError("Недопустимый запрос выполнил SQL")


@pytest.mark.parametrize("table", ('news"; DROP TABLE alumni; --', "club_settings", "mediafile", "unknown", ""))
async def test_unapproved_table_rejected_before_any_database_access(table):
    store = Store(NoDatabaseAccess())
    for operation in (
        lambda: store.read(table, fields=()),
        lambda: store.create(table, {}),
        lambda: store.update(table, {}, id="synthetic-id"),
        lambda: store.delete(table, id="synthetic-id"),
        lambda: store.aggregate(table),
    ):
        with pytest.raises(ValueError):
            await operation()
    for operation in (
        lambda: check_filters(table, None),
        lambda: check_fields(table),
        lambda: model_for(table),
    ):
        with pytest.raises(ValueError):
            operation()


async def test_sql_structure_and_django_keywords_rejected_before_database_access():
    store = Store(NoDatabaseAccess())
    injected = 'title" DESC; DROP TABLE news; --'
    for operation in (
        lambda: store.read("news", fields=(injected,)),
        lambda: store.read("news", sort=(injected,)),
        lambda: store.read("news", filters={"_connector": "OR 1=1 --"}),
        lambda: store.read("news", filters={"_negated": True}),
        lambda: store.read("news", filters={"title__regex": {"_eq": ".*"}}),
        lambda: store.read("news", filters={"_or": [{"title": {"_eq": "x"}}, {"_connector": "OR"}]}),
        lambda: store.read("news", filters={"title": {"_eq OR 1=1 --": "x"}}),
        lambda: store.create("news", {injected: "x"}),
        lambda: store.update("news", {injected: "x"}, id="synthetic-id"),
        lambda: store.delete("news", filters={"_connector": "OR"}),
        lambda: store.aggregate("news", group=(injected,)),
        lambda: store.aggregate("news", sum_field=injected),
    ):
        with pytest.raises(ValueError):
            await operation()
    with pytest.raises(ValueError):
        condition("news", {"_connector": "OR 1=1 --"})


@pytest.mark.parametrize("payload", PAYLOADS)
async def test_sql_payload_remains_literal_across_read_write_delete_and_aggregate(database_app, payload):
    state = database_app.state
    target = await state.store.create(
        "news", {"slug": str(uuid4()), "title": payload, "body": "target", "status": "published"}
    )
    sentinel = await state.store.create(
        "news", {"slug": str(uuid4()), "title": "Вторая новость", "body": "untouched", "status": "draft"}
    )
    filters = {"title": {"_eq": payload}}
    for operator, value in (("_eq", payload), ("_in", [payload]), ("_contains", payload), ("_icontains", payload)):
        found = await state.store.read("news", filters={"title": {operator: value}})
        assert [row["id"] for row in found] == [target["id"]]
    found = await state.database.rows("SELECT id FROM news WHERE title=%s", (payload,))
    assert [str(row["id"]) for row in found] == [target["id"]]
    await state.store.update("news", {"body": payload}, filters=filters)
    assert (await state.store.one("news", target["id"]))["body"] == payload
    assert (await state.store.one("news", sentinel["id"]))["body"] == "untouched"
    grouped = await state.store.aggregate("news", filters=filters, group=("title",))
    assert grouped == [{"title": payload, "count": "1"}]
    await state.store.delete("news", filters=filters)
    assert [row["id"] for row in await state.store.read("news")] == [sentinel["id"]]


async def test_http_sql_payloads_do_not_bypass_visibility_search_or_staff_login(database_app):
    app = database_app
    _, headers = await account(app, "admin")
    draft = await app.state.store.create(
        "news", {"slug": "private-synthetic", "title": "Закрытая новость", "body": "private", "status": "draft"}
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        reply = await client.post("/admin/news", headers=headers, json={"title": PAYLOADS[1], "body": PAYLOADS[2]})
        assert reply.status_code == 200
        saved = await app.state.store.one("news", reply.json()["id"])
        assert saved["title"] == PAYLOADS[1] and saved["body"] == PAYLOADS[2]
        for payload in PAYLOADS:
            reply = await client.get("/news/" + quote(payload, safe=""))
            assert reply.status_code == 404 and "private" not in reply.text
            reply = await client.get("/admin/members", headers=headers, params={"q": payload})
            assert reply.status_code == 200 and reply.json()["items"] == []
        reply = await client.get("/admin/members", headers=headers, params={"status": "verified' OR 1=1 --"})
        assert reply.status_code == 400
        reply = await client.post("/auth/admin-login", json={"email": "x' OR '1'='1", "password": "synthetic"})
        assert reply.status_code in (400, 401)
        assert (await app.state.store.one("news", draft["id"]))["body"] == "private"


async def test_empty_record_id_never_removes_where_guard(database_app):
    store = database_app.state.store
    sentinel = await store.create("news", {"slug": "sentinel", "title": "Сохранить", "body": "untouched"})
    for operation in (
        lambda: store.update("news", {"body": "changed"}, id=""),
        lambda: store.delete("news", id=""),
    ):
        with pytest.raises((ApiError, ValidationError, DatabaseError)):
            await operation()
        assert (await store.one("news", sentinel["id"]))["body"] == "untouched"
