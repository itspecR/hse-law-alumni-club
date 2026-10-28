import os

import httpx
import pytest

from club_api.asgi import initialize_django
from club_api.core.config import Settings
from club_api.main import create_app
from club_api.modules.media.service import FileResponse


async def test_django_validation_methods_and_error_redaction(monkeypatch, tmp_path, caplog):
    app = create_app(
        Settings(AUTH_SECRET="synthetic-django-session-secret-for-tests", UPLOADS_PATH=tmp_path, JOBS_ENABLED="false")
    )

    async def records(*args, **kwargs):
        return []

    monkeypatch.setattr(app.state.store, "read", records)
    async with (
        app.lifespan(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as client,
    ):
        assert (await client.get("/news?limit=1")).json() == []
        assert (await client.get("/news?limit=0")).status_code == 400
        many = "&".join(f"field{index}=value" for index in range(101))
        assert (await client.get("/news?" + many)).status_code == 400
        assert (await client.post("/admin/products", content=b"invalid-json")).status_code == 401
        assert (await client.post("/health")).status_code == 405
        assert (await client.head("/health")).content == b""

        async def failure(*args, **kwargs):
            raise RuntimeError("synthetic-secret-that-must-not-leak")

        monkeypatch.setattr(app.state.store, "read", failure)
        response = await client.get("/news")
        assert response.status_code == 500
        assert response.json() == {"error": "Внутренняя ошибка."}
        assert "synthetic-secret" not in response.text + caplog.text


def test_django_stream_closes_descriptor_once(tmp_path):
    initialize_django()
    file = tmp_path / "synthetic-audio.bin"
    file.write_bytes(b"audio")
    descriptor = os.open(file, os.O_RDONLY)

    async def chunks():
        yield b"audio"

    response = FileResponse(chunks(), descriptor=descriptor)
    response.close()
    response.close()
    with pytest.raises(OSError):
        os.fstat(descriptor)
