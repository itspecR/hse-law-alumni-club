import asyncio
import io
from uuid import uuid4

import httpx
from PIL import Image
from psycopg.types.json import Jsonb

from club_api.modules.auth.passwords import hash_password
from club_api.modules.support.routes import addition


async def account(app, role="alumni", *, verified=True):
    role_id, user_id, alumni_id = (str(uuid4()) for _ in range(3))
    hashed = await hash_password("synthetic-account-password")
    await app.state.database.execute("INSERT INTO directus_roles(id,name) VALUES(%s,%s)", (role_id, role))
    await app.state.database.execute(
        "INSERT INTO directus_users(id,email,password,role,status) VALUES(%s,%s,%s,%s,'active')",
        (user_id, user_id + "@example.test", hashed, role_id),
    )
    await app.state.database.execute(
        "INSERT INTO alumni(id,user_id,fio,cohort,edu_program,verification_status) VALUES(%s,%s,'Вымышленный участник','2020','Юриспруденция',%s)",
        (alumni_id, user_id, "verified" if verified else "pending"),
    )
    auth = app.state.auth
    token = auth.session(alumni_id, user_id) if role == "alumni" else auth.staff_session(user_id, role)
    return alumni_id, {"Authorization": "Bearer " + token}


async def test_event_attendance_replay_and_server_points(database_app):
    app = database_app
    alumni_id, member_headers = await account(app)
    _, editor_headers = await account(app, "editor")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/admin/events",
            headers=editor_headers,
            json={"title": "Тестовое событие", "starts_at": "2026-12-01T15:00:00Z", "points": 75},
        )
        assert response.status_code == 200, response.text
        event_id = response.json()["id"]
        assert (await client.post(f"/events/{event_id}/rsvp", headers=member_headers)).json() == {"going": True}
        roster = (await client.get(f"/admin/events/{event_id}/rsvps", headers=editor_headers)).json()
        results = await asyncio.gather(
            *(client.post(f"/admin/events/rsvp/{roster[0]['id']}/attend", headers=editor_headers) for _ in range(2))
        )
        assert all(response.status_code == 200 for response in results)
        assert (await app.state.store.one("alumni", alumni_id))["points_cached"] == 75
        assert len(await app.state.store.read("points_ledger", fields=("id",))) == 1
        assert (await client.post(f"/events/{event_id}/rsvp", headers=member_headers)).status_code == 400
        calendar = await client.get(f"/events/{event_id}.ics")
        assert calendar.status_code == 200 and "DTSTART:20261201T150000Z" in calendar.text
        assert (
            await client.patch(f"/admin/events/{event_id}", headers=editor_headers, json={"title": "Новое название"})
        ).status_code == 200
        event = await app.state.store.one("events", event_id)
        assert event["points"] == 75 and event["status"] == "published"


async def test_editor_cannot_grant_discounts_points_or_read_support(database_app):
    app = database_app
    alumni_id, member_headers = await account(app)
    _, editor_headers = await account(app, "editor")
    _, admin_headers = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        for method, path, body in [
            ("PATCH", f"/admin/members/{alumni_id}", {"personal_discount": 10}),
            ("POST", f"/admin/members/{alumni_id}/points", {"delta": 100}),
            ("POST", f"/admin/members/{alumni_id}/podcast-sub", {}),
            ("POST", f"/admin/members/{alumni_id}/anonymize", {}),
        ]:
            assert (await client.request(method, path, headers=editor_headers, json=body)).status_code == 403
        assert (await client.get("/admin/support", headers=editor_headers)).status_code == 403
        assert (await client.get("/admin/orders/export.csv", headers=editor_headers)).status_code == 403
        assert (await client.get("/admin/bot-status", headers=editor_headers)).status_code == 200
        assert (
            await client.post(f"/admin/members/{alumni_id}/points", headers=admin_headers, json={"delta": 100})
        ).status_code == 200
        assert (await client.get("/me", headers=member_headers)).status_code == 200


async def test_support_idempotency_access_consent_and_message_limit(database_app):
    app = database_app
    _, admin_headers = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        cfg = (await client.get("/support/config")).json()
        id, key = str(uuid4()), "a" * 64
        body = {
            "id": id,
            "key": key,
            "topic": "other",
            "message": "Тестовое обращение",
            "consent": True,
            "consentVersion": cfg["version"],
        }
        assert (await client.post("/support", json=body)).status_code == 200
        assert (await client.post("/support", json=body)).status_code == 200
        assert (await client.post("/support", json={**body, "message": "Другое сообщение"})).status_code == 409
        headers = {"X-Support-Key": key}
        assert (await client.get(f"/support/{id}", headers={"X-Support-Key": "b" * 64})).status_code == 404
        response = await client.get(f"/support/{id}", headers=headers)
        assert (
            response.headers["cache-control"] == "no-store"
            and response.json()["messages"][0]["text"] == body["message"]
        )
        assert (
            await client.patch(f"/admin/support/{id}", headers=admin_headers, json={"status": "answered"})
        ).status_code == 400
        await app.state.database.execute(
            "UPDATE club_support_tickets SET messages=%s WHERE id=%s",
            (Jsonb(addition("Сообщение до лимита", "visitor").obj * 50), id),
        )
        assert (
            await client.post(f"/support/{id}/messages", headers=headers, json={"message": "За лимитом"})
        ).status_code == 409
        assert (
            await client.patch(
                f"/admin/support/{id}",
                headers=admin_headers,
                json={"status": "answered", "message": "Ответ за лимитом"},
            )
        ).status_code == 404
        assert (
            await client.patch(f"/admin/support/{id}", headers=admin_headers, json={"status": "closed"})
        ).status_code == 200
        assert (
            await client.post(f"/support/{id}/messages", headers=headers, json={"message": "Новый вопрос"})
        ).status_code == 409
        assert (await client.delete(f"/support/{id}", headers=headers)).status_code == 200
        assert (await client.get(f"/support/{id}", headers=headers)).status_code == 404


def png():
    output = io.BytesIO()
    Image.new("RGB", (10, 20), "red").save(output, format="PNG", pnginfo=None)
    return output.getvalue()


async def test_media_references_range_avatar_privacy_and_symlink(database_app):
    app = database_app
    _, editor_headers = await account(app, "editor")
    alumni_id, member_headers = await account(app)
    data = png()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/admin/media", headers=editor_headers, files={"file": ("../test.png", data, "image/png")}
        )
        assert response.status_code == 201, response.text
        id = response.json()["id"]
        assert (await client.get(f"/media/{id}")).status_code == 404
        event = await app.state.store.create(
            "events",
            {"title": "Материал с фото", "starts_at": "2026-12-01T15:00:00Z", "cover": id, "status": "published"},
        )
        response = await client.get(f"/media/{id}", headers={"Range": "bytes=0-7"})
        assert response.status_code == 206 and response.content == data[:8]
        assert "sandbox" in response.headers["content-security-policy"]
        assert (await client.delete(f"/admin/media/{id}", headers=editor_headers)).status_code == 409
        await app.state.store.update("events", {"cover": None}, id=event["id"])
        assert (await client.delete(f"/admin/media/{id}", headers=editor_headers)).status_code == 200
        avatar = await client.post(
            "/me/avatar", headers=member_headers, files={"file": ("photo.png", data, "image/png")}
        )
        assert avatar.status_code == 200, avatar.text
        avatar_id = avatar.json()["avatar"]
        assert (await client.get(f"/media/{avatar_id}")).status_code == 404
        assert (await client.get(f"/admin/media/{avatar_id}/content", headers=editor_headers)).status_code == 404
        thumbnail = await client.get(f"/avatars/{avatar_id}")
        assert thumbnail.status_code == 200 and Image.open(io.BytesIO(thumbnail.content)).size == (10, 20)
        metadata = await app.state.media.get(avatar_id)
        path = app.state.settings.UPLOADS_PATH / metadata["filename_disk"]
        outside = app.state.settings.UPLOADS_PATH / "outside.png"
        outside.write_bytes(data)
        path.unlink()
        path.symlink_to(outside)
        assert (await client.get(f"/avatars/{avatar_id}")).status_code == 404
        assert (await app.state.store.one("alumni", alumni_id))["avatar"] == avatar_id


async def test_content_patch_keeps_defaults_and_canonical_server_price(database_app):
    app = database_app
    _, editor_headers = await account(app, "editor")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        response = await client.post(
            "/admin/products",
            headers=editor_headers,
            json={"title": "Тестовый товар", "category": "Мерч", "price": 10000, "stock": 5},
        )
        assert response.status_code == 200, response.text
        id = response.json()["id"]
        assert (
            await client.patch(f"/admin/products/{id}", headers=editor_headers, json={"title": "Обновлённый товар"})
        ).status_code == 200
        row = await app.state.store.one("products", id)
        assert (row["price"], row["stock"], row["status"]) == (10000, 5, "published")
        assert (
            await client.patch(f"/admin/products/{id}", headers=editor_headers, json={"price": True})
        ).status_code == 400


async def test_friend_requests_privacy_acceptance_and_removal(database_app):
    app = database_app
    first, first_headers = await account(app)
    second, second_headers = await account(app)
    pending, _ = await account(app, verified=False)
    await app.state.store.update("alumni", {"contacts_json": {"email": "private@example.test"}}, id=second)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        assert (await client.post("/me/friends", headers=first_headers, json={"alumni_id": first})).status_code == 400
        assert (await client.post("/me/friends", headers=first_headers, json={"alumni_id": pending})).status_code == 404
        for _ in range(2):
            assert (await client.post("/me/friends", headers=first_headers, json={"alumni_id": second})).json() == {
                "status": "pending"
            }
        rows = (await client.get("/me/classmates", headers=first_headers)).json()
        assert len(rows) == 1 and rows[0]["id"] == second and rows[0]["friend_status"] == "pending"
        assert "private@example.test" not in str(rows) and "user_id" not in rows[0]
        notices = (await client.get("/me/events", headers=second_headers)).json()
        assert notices[0]["kind"] == "friend_request" and notices[0]["from_id"] == first
        assert (await client.post("/me/friends", headers=second_headers, json={"alumni_id": first})).json() == {
            "status": "accepted"
        }
        assert len(await app.state.store.read("alumni_friends", fields=("id",))) == 1
        assert (await client.delete("/me/friends/" + second, headers=first_headers)).json() == {"status": "none"}
        assert not await app.state.store.read("alumni_friends", fields=("id",))


async def test_self_delete_and_admin_anonymize_revoke_access_and_remove_data(database_app):
    app = database_app
    member, headers = await account(app)
    other, other_headers = await account(app)
    _, admin_headers = await account(app, "admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
        uploaded = await client.post("/me/avatar", headers=headers, files={"file": ("photo.png", png(), "image/png")})
        avatar = uploaded.json()["avatar"]
        await client.post("/me/friends", headers=headers, json={"alumni_id": other})
        await app.state.store.update("alumni", {"contacts_json": {"phone": "+7 000 0000000"}}, id=member)
        exported = await client.get("/me/export", headers=headers)
        assert exported.status_code == 200 and "password" not in exported.text
        assert exported.json()["profile"]["contacts_json"]["phone"] == "+7 000 0000000"
        assert (await client.post("/me/delete", headers=headers, json={"confirm": "удалить"})).status_code == 400
        assert (await client.post("/me/delete", headers=headers, json={"confirm": "УДАЛИТЬ"})).status_code == 200
        assert (await client.get("/me", headers=headers)).status_code == 401
        assert (await client.get("/avatars/" + avatar)).status_code == 404
        member_row = await app.state.store.one("alumni", member)
        assert member_row["fio"] == "Удаленный участник"
        assert member_row["contacts_json"] is None and member_row["user_id"] is None
        assert not await app.state.store.read("alumni_friends", fields=("id",))
        assert (await client.get("/me", headers=other_headers)).status_code == 200
        assert (await client.post("/admin/members/" + other + "/anonymize", headers=admin_headers)).status_code == 200
        assert (await client.get("/me", headers=other_headers)).status_code == 401
