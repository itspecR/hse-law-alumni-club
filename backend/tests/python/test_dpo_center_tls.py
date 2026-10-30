import base64
import json
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from test_dpo_center import card, jpeg, programs

from club_api.core.errors import ApiError
from club_api.core.outgoing import UNAVAILABLE
from club_api.modules.catalog import center

PROGRAMS = 3
PUBLIC = "https://62.112.123.10:8443"


def issue_certificate(folder, name):
    certificate, key = folder / f"{name}.pem", folder / f"{name}.key"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=Центр ДПО",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-addext",
            "keyUsage=digitalSignature,keyEncipherment",
            "-addext",
            "extendedKeyUsage=serverAuth",
        ],
        check=True,
        capture_output=True,
    )
    return certificate, key


def encoded(certificate):
    return base64.b64encode(certificate.read_bytes()).decode()


def stamped(number):
    return card(str(number), f"Программа {number}", thumb={"src": f"/media/programs/thumbs/{number}.jpg?v=1761800000"})


class CenterSite(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == center.CATALOG_PATH:
            self.answer("application/json", json.dumps(self.server.feed).encode())
        elif urlsplit(self.path).path.endswith(".jpg"):
            self.answer("image/jpeg", jpeg())
        else:
            self.send_error(404)

    def answer(self, kind, body):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return None


@pytest.fixture
def center_site(tmp_path):
    certificate, key = issue_certificate(tmp_path, "center")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), CenterSite)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.feed = {"cards": [stamped(number) for number in range(101, 101 + PROGRAMS)]}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://127.0.0.1:{server.server_address[1]}", certificate
    server.shutdown()
    server.server_close()


@pytest.fixture
def center_app(database_app, monkeypatch):
    monkeypatch.setattr(center, "IMAGE_DELAY_SECONDS", 0)
    database_app.state.notifications.office_text = AsyncMock()
    return database_app


async def failure(app):
    with pytest.raises(ApiError) as error:
        await center.sync_center(app.state)
    return error.value.message


async def test_center_site_on_port_is_loaded_only_with_its_own_certificate(
    center_app, center_site, tmp_path, monkeypatch
):
    app, (address, certificate) = center_app, center_site
    settings = app.state.settings
    other, _ = issue_certificate(tmp_path, "other")
    monkeypatch.setattr(settings, "DPO_SITE_URL", address)
    monkeypatch.setattr(settings, "DPO_SITE_CERTIFICATE", "")
    assert await failure(app) == center.WRONG_ADDRESS
    monkeypatch.setattr(settings, "DPO_SITE_CERTIFICATE", encoded(other))
    assert await failure(app) == UNAVAILABLE
    monkeypatch.setattr(settings, "DPO_SITE_CERTIFICATE", "не сертификат")
    assert await failure(app) == center.NO_CERTIFICATE
    assert await programs(app) == {}
    monkeypatch.setattr(settings, "DPO_SITE_CERTIFICATE", encoded(certificate))
    monkeypatch.setattr(settings, "DPO_SITE_LINK_URL", PUBLIC)
    result = await center.sync_center(app.state)
    assert (result["found"], result["created"]) == (PROGRAMS, PROGRAMS)
    rows = list((await programs(app)).values())
    assert all(row["source_url"].startswith(PUBLIC + "/programs/") for row in rows)
    assert all(row["cover"] and row["cover_source"].startswith(address + "/media/") for row in rows)
    assert all(row["cover_source"].endswith("?v=1761800000") for row in rows)
