import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from club_api.modules.members.service import anonymize
from club_api.modules.notifications.mail import EMAIL_CONFIRMATION_KIND, Notifications, mail_user_lock


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


class MemoryDatabase:
    pool = True

    def __init__(self):
        self.user_id, self.alumni_id = str(uuid4()), str(uuid4())
        self.email = "canonical@example.test"
        self.users = {self.user_id: {"id": self.user_id, "email": self.email, "role_name": "alumni"}}
        self.profile = {"id": self.alumni_id, "user_id": self.user_id, "avatar": None, "education_document": None}
        self.outbox = {}
        self.locks = {}
        self.lock_trace = []
        self.snapshot_taken = None
        self.resume_snapshot = None

    def mail(self, id, *, owner=None, email=None, kind=EMAIL_CONFIRMATION_KIND):
        self.outbox[id] = {
            "id": id,
            "owner_user_id": owner,
            "to_addr": email or self.email,
            "kind": kind,
            "subject": "Вымышленная тема",
            "body": "Вымышленное письмо",
            "attempts": 0,
            "status": "pending",
        }

    @asynccontextmanager
    async def connection(self):
        connection = MemoryConnection(self)
        try:
            yield connection
        finally:
            for lock in reversed(connection.held):
                lock.release()

    transaction = connection

    async def rows(self, query, params=()):
        async with self.connection() as connection:
            result = await (await connection.execute(query, params)).fetchall()
        if query.startswith("SELECT id,kind,to_addr,owner_user_id") and self.snapshot_taken:
            self.snapshot_taken.set()
            await self.resume_snapshot.wait()
        return result


class MemoryConnection:
    def __init__(self, database):
        self.database = database
        self.held = []

    async def lock(self, key, *, wait=5):
        lock = self.database.locks.setdefault(key, asyncio.Lock())
        await lock.acquire()
        self.held.append(lock)
        self.database.lock_trace.append(key)
        return True

    async def execute(self, query, params=()):
        database = self.database
        if query.startswith("SELECT user_id FROM alumni"):
            return Cursor([{"user_id": database.profile["user_id"]}])
        if query.startswith("SELECT id,user_id,avatar,education_document FROM alumni"):
            database.lock_trace.append("profile-row")
            return Cursor([dict(database.profile)])
        if query.startswith("SELECT u.email,r.name"):
            database.lock_trace.append("user-row")
            user = database.users.get(params[0])
            return Cursor([dict(user)] if user else [])
        if query.startswith("SELECT u.id,u.email"):
            if "u.id=%s" in query:
                users = [database.users[params[0]]] if params[0] in database.users else []
            else:
                users = [user for user in database.users.values() if user["email"].lower() == params[0]]
            return Cursor([dict(user) for user in users])
        if query.startswith("SELECT id,kind,to_addr,owner_user_id"):
            return Cursor([dict(row) for row in database.outbox.values() if row["status"] == "pending"])
        if query.startswith("SELECT id,kind,to_addr,subject"):
            row = database.outbox.get(params[0])
            return Cursor([dict(row)] if row and row["status"] == "pending" else [])
        if query.startswith("INSERT INTO club_mail_outbox"):
            id = max(database.outbox, default=0) + 1
            kind, email, subject, body, owner = params
            database.mail(id, owner=owner, email=email, kind=kind)
            database.outbox[id].update(subject=subject, body=body)
            return Cursor([{"id": id}])
        if query.startswith("DELETE FROM club_mail_outbox"):
            if "WHERE id=%s" in query:
                database.outbox.pop(params[0], None)
            else:
                kind, value = params
                for id, row in list(database.outbox.items()):
                    matches = (
                        row["owner_user_id"] == value
                        if "owner_user_id=%s" in query
                        else (row["owner_user_id"] is None and row["to_addr"].lower() == value)
                    )
                    if row["kind"] == kind and matches:
                        del database.outbox[id]
            return Cursor()
        if query.startswith("UPDATE club_mail_outbox"):
            row = database.outbox[params[-1]]
            row["attempts"] = params[0]
            if "status='sent'" in query:
                row["status"] = "sent"
                if row["kind"] == EMAIL_CONFIRMATION_KIND:
                    row["body"] = ""
            elif "status='failed'" in query:
                row["status"] = "failed"
            return Cursor()
        if query.startswith("UPDATE alumni SET fio="):
            database.profile["user_id"] = None
            return Cursor()
        if query.startswith("DELETE FROM directus_users"):
            database.users.pop(params[0], None)
            return Cursor()
        if query.startswith(
            (
                "DELETE FROM club_social",
                "DELETE FROM club_email_codes",
                "DELETE FROM alumni_friends",
                "DELETE FROM push_subs",
                "UPDATE orders",
                "UPDATE club_donations",
                "UPDATE directus_users",
            )
        ):
            return Cursor()
        raise AssertionError(query)


def services(database):
    settings = SimpleNamespace(SMTP_HOST="synthetic.invalid", MAIL_OUTBOX_MAX_ATTEMPTS=8)
    notifications = Notifications(settings, database, None)
    state = SimpleNamespace(database=database, notifications=notifications, media=SimpleNamespace(delete=AsyncMock()))
    return state


async def test_anonymize_purges_owned_and_canonical_legacy_mail_before_stale_drain():
    database = MemoryDatabase()
    database.mail(1, owner=database.user_id)
    database.mail(2)
    database.mail(3, owner=str(uuid4()))
    database.mail(4, email="arbitrary-contact@example.test")
    database.mail(5, kind="office_order")
    database.profile["contacts_json"] = {"email": "arbitrary-contact@example.test"}
    database.snapshot_taken, database.resume_snapshot = asyncio.Event(), asyncio.Event()
    state = services(database)
    state.notifications.send_email = AsyncMock(return_value=True)
    drain = asyncio.create_task(state.notifications.drain_mail())
    await database.snapshot_taken.wait()
    assert await anonymize(state, database.alumni_id)
    assert set(database.outbox) == {3, 4, 5}
    database.resume_snapshot.set()
    await drain
    state.notifications.send_email.assert_awaited_once_with(database.email, "Вымышленная тема", "Вымышленное письмо")
    assert database.lock_trace[:3] == [mail_user_lock(database.user_id), "user-row", "profile-row"]


async def test_anonymize_waits_for_inflight_delivery_and_late_enqueue_is_blocked():
    database = MemoryDatabase()
    database.mail(1, owner=database.user_id)
    state = services(database)
    started, finish = asyncio.Event(), asyncio.Event()
    completed = []

    async def deliver(*args):
        started.set()
        await finish.wait()
        completed.append("sent")
        return True

    state.notifications.send_email = deliver
    drain = asyncio.create_task(state.notifications.drain_mail())
    await started.wait()
    deletion = asyncio.create_task(anonymize(state, database.alumni_id))
    await asyncio.sleep(0)
    assert not deletion.done()
    finish.set()
    await drain
    assert await deletion
    completed.append("deleted")
    assert completed == ["sent", "deleted"]
    assert not database.outbox
    queued = await state.notifications.enqueue_mail(
        database.email, "Тема", "Текст", kind=EMAIL_CONFIRMATION_KIND, owner_user_id=database.user_id
    )
    assert queued == {"id": None, "sent": False, "blocked": True}
    assert not database.outbox


async def test_confirmation_queue_uses_owner_email_and_serializes_concurrent_drains():
    database = MemoryDatabase()
    state = services(database)
    state.notifications.send_email = AsyncMock(return_value=False)
    assert (
        await state.notifications.enqueue_mail(
            "foreign@example.test", "Тема", "Текст", kind=EMAIL_CONFIRMATION_KIND, owner_user_id=database.user_id
        )
    )["blocked"]
    assert not database.outbox
    assert (
        await state.notifications.enqueue_mail(
            database.email.upper(), "Тема", "Текст", kind=EMAIL_CONFIRMATION_KIND, owner_user_id=database.user_id
        )
    )["id"] == 1
    assert database.outbox[1]["owner_user_id"] == database.user_id
    assert database.outbox[1]["to_addr"] == database.email
    state.notifications.send_email = AsyncMock(return_value=True)
    await asyncio.gather(state.notifications.drain_mail(), state.notifications.drain_mail())
    state.notifications.send_email.assert_awaited_once()
    assert database.outbox[1]["status"] == "sent" and database.outbox[1]["body"] == ""


async def test_database_anonymize_cancels_confirmation_without_erasing_contact_or_office_mail(
    database_app, monkeypatch
):
    from test_auth_integration import fixture

    from club_api.modules.auth.routes import queue_confirmation

    app = database_app
    user_id, alumni_id = await fixture(app)
    other_user, _ = await fixture(app)
    email, other_email = user_id + "@example.test", other_user + "@example.test"
    sender = AsyncMock(return_value=False)
    monkeypatch.setattr(app.state.notifications, "send_email", sender)
    monkeypatch.setattr(app.state.notifications.settings, "SMTP_HOST", "synthetic.invalid")
    await queue_confirmation(SimpleNamespace(services=app.state), user_id, email)
    rows = await app.state.database.rows("SELECT owner_user_id,to_addr FROM club_mail_outbox")
    assert str(rows[0]["owner_user_id"]) == user_id and rows[0]["to_addr"] == email
    for kind, address in [
        (EMAIL_CONFIRMATION_KIND, email),
        (EMAIL_CONFIRMATION_KIND, other_email),
        ("office_order", email),
    ]:
        await app.state.database.execute(
            "INSERT INTO club_mail_outbox(kind,to_addr,subject,body) VALUES(%s,%s,'Тема','Текст')", (kind, address)
        )
    await app.state.store.update("alumni", {"contacts_json": {"email": other_email}}, id=alumni_id)
    assert await anonymize(app.state, alumni_id)
    remaining = await app.state.database.rows("SELECT kind,to_addr FROM club_mail_outbox")
    assert {(row["kind"], row["to_addr"]) for row in remaining} == {
        (EMAIL_CONFIRMATION_KIND, other_email),
        ("office_order", email),
    }
    sender.assert_awaited_once()
