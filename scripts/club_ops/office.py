from urllib.parse import urlsplit

import httpx

from club_ops.bootstrap import OperatorError


class Office:
    def __init__(self, values):
        base = values.get("CLUB_API_URL", "http://localhost")
        try:
            url = urlsplit(base)
            valid = url.scheme == "https" or url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")
            if (
                not valid
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
            ):
                raise ValueError
            _ = url.port
        except ValueError:
            raise OperatorError("Нужен корневой CLUB_API_URL с HTTPS либо локальным HTTP") from None
        token = values.get("CLUB_ADMIN_TOKEN")
        if not token:
            raise OperatorError("Задайте CLUB_ADMIN_TOKEN через окружение")
        self.client = httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"authorization": "Bearer " + token},
            timeout=300,
            follow_redirects=False,
            trust_env=False,
        )

    async def request(self, path, method="GET", **kwargs):
        if not path or path.startswith("/") or ".." in path or ":" in path:
            raise OperatorError("Некорректный путь команды панели управления")
        response = await self.client.request(method, "/api/admin/" + path, **kwargs)
        if not response.is_success:
            raise OperatorError(f"Операция панели управления завершилась с HTTP {response.status_code}")
        return response.json()

    async def close(self):
        await self.client.aclose()
