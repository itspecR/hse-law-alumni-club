import asyncio
import re
import time

from club_api.core.errors import ApiError

OAUTH_PATH = "/oauth/token"
RECEIPTS_PATH = "/protected/documents/registration/receipts"
DOCUMENTS_PATH = "/protected/documents/"
CONNECTOR_CLIENT = "processing-connector-token"
TOKEN_RENEWAL_MARGIN_SECONDS = 60
UNAUTHORIZED = 401
STATUSES = frozenset(("NEW", "QUEUE", "REGISTRATION", "SUCCESS", "FAIL"))
IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9-]{1,64}")
DOCUMENT_ID = re.compile(r"[0-9]{1,20}")
UNAVAILABLE = "Онлайн-касса не настроена."
FAILURE = "Не удалось получить ответ онлайн-кассы."


class Bifit:
    def __init__(self, settings, client, clock=time.monotonic):
        self.settings = settings
        self.client = client
        self.clock = clock
        self.lock = asyncio.Lock()
        self.access = None
        self.refresh = None
        self.expires = 0.0

    @property
    def enabled(self):
        return bool(self.settings.BIFIT_API_URL and self.settings.secret("BIFIT_CONNECTOR_TOKEN"))

    def url(self, path):
        return self.settings.BIFIT_API_URL + path

    async def grant(self, form):
        response = await self.client.post(
            self.url(OAUTH_PATH), data={"client_id": CONNECTOR_CLIENT, "client_secret": CONNECTOR_CLIENT, **form}
        )
        body = response.json()
        if (
            not response.is_success
            or not isinstance(body, dict)
            or not isinstance(body.get("access_token"), str)
            or type(body.get("expires_in")) is not int
        ):
            raise ValueError
        self.access = body["access_token"]
        self.refresh = body["refresh_token"] if isinstance(body.get("refresh_token"), str) else None
        self.expires = self.clock() + max(body["expires_in"] - TOKEN_RENEWAL_MARGIN_SECONDS, 0)

    async def renew(self):
        if self.refresh:
            try:
                return await self.grant({"grant_type": "refresh_token", "refresh_token": self.refresh})
            except Exception:
                self.refresh = None
        await self.grant({"grant_type": "token", "token": self.settings.secret("BIFIT_CONNECTOR_TOKEN")})

    async def token(self):
        async with self.lock:
            if not self.access or self.clock() >= self.expires:
                await self.renew()
            return self.access

    def forget(self):
        self.access = None
        self.expires = 0.0

    async def send(self, method, path, headers, options):
        token = await self.token()
        return await self.client.request(
            method, self.url(path), headers={"authorization": "Bearer " + token, **headers}, **options
        )

    async def call(self, method, path, headers=None, **options):
        if not self.enabled:
            raise ApiError(503, UNAVAILABLE)
        try:
            response = await self.send(method, path, headers or {}, options)
            if response.status_code == UNAUTHORIZED:
                self.forget()
                response = await self.send(method, path, headers or {}, options)
            if not response.is_success:
                raise ValueError
            return response.json()
        except Exception as error:
            raise ApiError(502, FAILURE) from error

    async def register(self, receipt, key):
        if not isinstance(key, str) or not IDEMPOTENCY_KEY.fullmatch(key):
            raise ApiError(400, "Некорректный ключ документа кассы.")
        document = await self.call("POST", RECEIPTS_PATH, headers={"idempotency-key": key}, json=receipt)
        if (
            isinstance(document, bool)
            or not isinstance(document, int | str)
            or not DOCUMENT_ID.fullmatch(str(document))
        ):
            raise ApiError(502, FAILURE)
        return str(document)

    async def document(self, id):
        if not isinstance(id, str) or not DOCUMENT_ID.fullmatch(id):
            raise ApiError(400, "Некорректный номер документа кассы.")
        document = await self.call("GET", DOCUMENTS_PATH + id)
        if not isinstance(document, dict) or document.get("status") not in STATUSES:
            raise ApiError(502, FAILURE)
        return document
