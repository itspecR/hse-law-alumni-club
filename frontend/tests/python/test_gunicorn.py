import asyncio
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from club_web.views import PUBLIC


def test_gunicorn_serves_parallel_assets_with_reused_connections():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "WEB_HOST": "127.0.0.1",
        "WEB_PORT": str(port),
        "API_INTERNAL_URL": "http://127.0.0.1:9",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "club_web.main"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    base = "http://127.0.0.1:" + str(port)
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                assert httpx.get(base + "/health", timeout=1, trust_env=False).status_code == 200
                break
            except httpx.HTTPError:
                if time.monotonic() > deadline:
                    pytest.fail("Gunicorn не запустился")
                time.sleep(0.05)
        files = [path for path in sorted((PUBLIC / "assets").rglob("*.webp")) if path.is_file()][:12]
        assert len(files) >= 10

        async def requests():
            async with httpx.AsyncClient(
                base_url=base,
                timeout=3,
                trust_env=False,
                limits=httpx.Limits(max_connections=6, max_keepalive_connections=6),
            ) as client:

                async def asset(path):
                    response = await client.get("/" + str(path.relative_to(PUBLIC)))
                    assert response.status_code == 200
                    assert response.content == path.read_bytes()
                    assert int(response.headers["content-length"]) == len(response.content)

                for _ in range(3):
                    await asyncio.gather(*(asset(path) for path in files))

        asyncio.run(requests())
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
