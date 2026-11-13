import httpx
import pytest
from test_community_integration import account, png

from club_api.modules.members import education
from club_api.modules.members.service import anonymize

PATH = "/me/education-document"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
MP3 = b"ID3" + bytes(64)


@pytest.fixture(autouse=True)
def upload_enabled(request, monkeypatch):
    if "database_app" in request.fixturenames:
        app = request.getfixturevalue("database_app")
        monkeypatch.setattr(app.state.settings, "EDUCATION_DOCUMENT_ENABLED", "true")


def browser(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")


def pdf():
    return {"file": ("diploma.pdf", PDF, "application/pdf")}


async def stored(app, alumni_id):
    document = (await app.state.store.one("alumni", alumni_id))["education_document"]
    files = [str(row["id"]) for row in await app.state.database.rows("SELECT id FROM directus_files")]
    return document, files, len(list(app.state.settings.UPLOADS_PATH.iterdir()))


async def test_pending_alumni_attaches_replaces_and_removes_document(database_app):
    app = database_app
    alumni_id, headers = await account(app, verified=False)
    async with browser(app) as client:
        profile = (await client.get("/me", headers=headers)).json()["alumni"]
        assert (profile["education_document"], profile["education_upload"]) == (False, True)
        first = await client.post(PATH, headers=headers, files=pdf())
        assert first.status_code == 200 and first.json() == {"ok": True}, first.text
        first_id, files, on_disk = await stored(app, alumni_id)
        assert files == [first_id] and on_disk == 1
        assert (await app.state.media.get(first_id))["filename_disk"] == first_id + ".pdf"
        assert (await client.get("/me", headers=headers)).json()["alumni"]["education_document"] is True
        second = await client.post(PATH, headers=headers, files={"file": ("photo.png", png(), "image/png")})
        assert second.status_code == 200, second.text
        current, files, on_disk = await stored(app, alumni_id)
        assert current != first_id and files == [current] and on_disk == 1
        assert (await client.delete(PATH, headers=headers)).status_code == 200
        assert await stored(app, alumni_id) == (None, [], 0)
        missing = await client.delete(PATH, headers=headers)
        assert missing.status_code == 404 and missing.json()["error"] == education.MISSING


async def test_document_upload_refuses_wrong_status_type_and_size(database_app, monkeypatch):
    app = database_app
    verified_id, verified = await account(app)
    pending_id, pending = await account(app, verified=False)
    async with browser(app) as client:
        assert (await client.post(PATH, files=pdf())).status_code == 401
        late = await client.post(PATH, headers=verified, files=pdf())
        assert late.status_code == 403 and late.json()["error"] == education.ONLY_PENDING
        assert (await client.get("/me", headers=verified)).json()["alumni"]["education_upload"] is False
        assert (await client.delete(PATH, headers=verified)).status_code == 403
        for name, content in (("notes.txt", b"plain text, not a document"), ("song.mp3", MP3)):
            wrong = await client.post(PATH, headers=pending, files={"file": (name, content, "application/pdf")})
            assert wrong.status_code == 400 and wrong.json()["error"] == education.WRONG_TYPE, name
        two = await client.post(PATH, headers=pending, files=[("a", ("a.pdf", PDF)), ("b", ("b.pdf", PDF))])
        assert two.status_code == 400
        monkeypatch.setattr(education, "MAX_BYTES", len(PDF) - 1)
        large = await client.post(PATH, headers=pending, files=pdf())
        assert large.status_code == 400 and large.json()["error"] == education.TOO_LARGE
    assert await stored(app, pending_id) == (None, [], 0)
    assert (await stored(app, verified_id))[0] is None


async def test_only_full_admin_reads_document_and_media_library_never_shows_it(database_app):
    app = database_app
    alumni_id, member = await account(app, verified=False)
    _, editor = await account(app, "editor")
    _, admin = await account(app, "admin")
    async with browser(app) as client:
        assert (await client.post(PATH, headers=member, files=pdf())).status_code == 200
        document = (await stored(app, alumni_id))[0]
        address = f"/admin/members/{alumni_id}/education-document"
        assert (await client.get(address)).status_code == 401
        assert (await client.get(address, headers=member)).status_code in (401, 403)
        assert (await client.get(address, headers=editor)).status_code == 403
        opened = await client.get(address, headers=admin)
        assert opened.status_code == 200 and opened.content == PDF
        assert opened.headers["content-type"] == "application/pdf"
        assert opened.headers["content-disposition"] == 'attachment; filename="education-document.pdf"'
        assert opened.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in opened.headers["content-security-policy"]
        assert opened.headers["cache-control"].startswith("private, no-store")
        assert (await client.get(f"/media/{document}")).status_code == 404
        assert (await client.get(f"/avatars/{document}")).status_code == 404
        assert (await client.get(f"/admin/media/{document}/content", headers=admin)).status_code == 404
        assert (await client.delete(f"/admin/media/{document}", headers=admin)).status_code == 404
        library = (await client.get("/admin/media", headers=admin)).json()
        assert library["total"] == 0 and library["items"] == []
        listed = (await client.get("/admin/members?status=pending", headers=editor)).json()["items"]
        assert [row["education_document"] for row in listed if row["id"] == alumni_id] == [True]
        event = {"title": "Событие с чужим файлом", "starts_at": "2026-12-01T15:00:00Z", "cover": document}
        misuse = await client.post("/admin/events", headers=editor, json=event)
        assert misuse.status_code == 400 and "Документ об образовании нельзя использовать" in misuse.json()["error"]
        assert (await stored(app, alumni_id))[0] == document


@pytest.mark.parametrize("decision", ["verified", "rejected"])
async def test_verification_decision_deletes_document(database_app, decision):
    app = database_app
    alumni_id, member = await account(app, verified=False)
    _, admin = await account(app, "admin")
    async with browser(app) as client:
        assert (await client.post(PATH, headers=member, files=pdf())).status_code == 200
        kept = await client.patch(f"/admin/members/{alumni_id}", headers=admin, json={"personal_discount": 5})
        assert kept.status_code == 200 and (await stored(app, alumni_id))[0]
        decided = await client.patch(
            f"/admin/members/{alumni_id}", headers=admin, json={"verification_status": decision}
        )
        assert decided.status_code == 200, decided.text
        assert await stored(app, alumni_id) == (None, [], 0)
        assert (await client.get(f"/admin/members/{alumni_id}/education-document", headers=admin)).status_code == 404
        assert (await client.post(PATH, headers=member, files=pdf())).status_code == 403


async def test_profile_erasure_deletes_document(database_app):
    app = database_app
    alumni_id, member = await account(app, verified=False)
    async with browser(app) as client:
        assert (await client.post(PATH, headers=member, files=pdf())).status_code == 200
    assert await anonymize(app.state, alumni_id)
    assert await stored(app, alumni_id) == (None, [], 0)


async def test_switched_off_upload_refuses_files_but_staff_still_reads_and_decision_deletes(database_app, monkeypatch):
    app = database_app
    alumni_id, member = await account(app, verified=False)
    _, admin = await account(app, "admin")
    address = f"/admin/members/{alumni_id}/education-document"
    async with browser(app) as client:
        assert (await client.post(PATH, headers=member, files=pdf())).status_code == 200
        monkeypatch.setattr(app.state.settings, "EDUCATION_DOCUMENT_ENABLED", "false")
        for response in (
            await client.post(PATH, headers=member, files=pdf()),
            await client.delete(PATH, headers=member),
        ):
            assert response.status_code == 403 and response.json()["error"] == education.DISABLED
        profile = (await client.get("/me", headers=member)).json()["alumni"]
        assert (profile["education_document"], profile["education_upload"]) == (True, False)
        assert (await client.get(address, headers=admin)).status_code == 200
        decided = await client.patch(
            f"/admin/members/{alumni_id}", headers=admin, json={"verification_status": "rejected"}
        )
        assert decided.status_code == 200 and await stored(app, alumni_id) == (None, [], 0)
