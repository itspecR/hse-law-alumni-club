import asyncio
import ipaddress
import json
import logging
import socket
from urllib.parse import urlsplit

import aiohttp
from django.http import HttpRequest
from py_vapid import Vapid
from pydantic import Field, field_validator
from pywebpush import WebPushException, webpush_async

from club_api.core.schemas import Body
from club_api.core.views import api_view, parse_body
from club_api.db.queries import acquire_lock
from club_api.modules.auth.service import require_alumni
from club_api.modules.gamification.routes import verified_alumni
from club_api.observability.audit import audit

ROUTE_LIMITS = {"/me/push/subscribe": 10}
logger = logging.getLogger("club.push")


class PublicResolver(aiohttp.abc.AbstractResolver):
    def __init__(self):
        self.resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await self.resolver.resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(row["host"]).is_global for row in addresses):
            raise OSError("Адрес уведомлений недоступен")
        return addresses

    async def close(self):
        await self.resolver.close()


class PushSession:
    def __init__(self, session):
        self.session = session

    async def post(self, endpoint, *, timeout, **params):
        EndpointBody.endpoint_valid(endpoint)
        return await self.session.post(
            endpoint, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=False, **params
        )


class EndpointBody(Body):
    endpoint: str = Field(max_length=1000)

    @field_validator("endpoint")
    @classmethod
    def endpoint_valid(cls, value):
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or (url.hostname == "localhost")
            or url.hostname.endswith((".local", ".internal"))
        ):
            raise ValueError("Некорректный адрес уведомлений")
        try:
            address = ipaddress.ip_address(url.hostname)
        except ValueError:
            address = None
        if address is not None and (not address.is_global):
            raise ValueError("Некорректный адрес уведомлений")
        return value


class PushKeys(Body):
    p256dh: str = Field(min_length=10)
    auth: str = Field(min_length=5)


class SubscribeBody(EndpointBody):
    keys: PushKeys


class Push:
    def __init__(self, state):
        self.state = state
        self.slots = asyncio.Semaphore(4)

    @property
    def enabled(self):
        return bool(self.state.settings.VAPID_PUBLIC_KEY and self.state.settings.secret("VAPID_PRIVATE_KEY"))

    async def send(self, filters, payload):
        if not self.enabled:
            return 0
        subscriptions = await self.state.store.read(
            "push_subs", filters=filters, fields=("id", "endpoint", "keys"), limit=-1
        )
        connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=4)
        async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
            for subscription in subscriptions:
                try:
                    EndpointBody.endpoint_valid(subscription["endpoint"])
                    async with self.slots:
                        await webpush_async(
                            subscription_info={"endpoint": subscription["endpoint"], "keys": subscription["keys"]},
                            data=json.dumps(payload, ensure_ascii=False),
                            vapid_private_key=Vapid.from_string(self.state.settings.secret("VAPID_PRIVATE_KEY")),
                            vapid_claims={"sub": "mailto:" + (self.state.settings.SMTP_FROM or "club@pravo.hse.ru")},
                            ttl=3600,
                            timeout=10,
                            aiohttp_session=PushSession(session),
                        )
                except WebPushException as error:
                    if error.response is not None and error.response.status in (404, 410):
                        await self.state.store.delete("push_subs", id=subscription["id"])
                    else:
                        logger.warning("Push-уведомление не доставлено")
                except Exception:
                    logger.warning("Push-уведомление не доставлено")
        return len(subscriptions)

    async def to_alumni(self, id, payload):
        return await self.send({"alumni_id": {"_eq": id}}, payload)

    async def to_many(self, ids, payload):
        return await self.send({"alumni_id": {"_in": list(dict.fromkeys(ids))}}, payload) if ids else 0

    async def to_all(self, payload):
        return await self.send(None, payload)


@api_view
async def vapid(request: HttpRequest):
    return {"enabled": request.services.push.enabled, "key": request.services.settings.VAPID_PUBLIC_KEY or None}


@api_view(body=SubscribeBody)
async def subscribe(request: HttpRequest):
    alumni = await verified_alumni(request)
    body = parse_body(request, SubscribeBody)
    state = request.services
    previous = None
    async with state.database.transaction() as connection:
        await acquire_lock(connection, "push:" + body.endpoint)
        rows = await state.store.read(
            "push_subs",
            filters={"endpoint": {"_eq": body.endpoint}},
            fields=("id", "alumni_id"),
            limit=1,
            connection=connection,
        )
        data = {"alumni_id": alumni["id"], "endpoint": body.endpoint, "keys": body.keys.model_dump()}
        if rows:
            previous = rows[0]["alumni_id"]
            await state.store.update("push_subs", data, id=rows[0]["id"], connection=connection)
        else:
            await state.store.create("push_subs", data, connection=connection)
    if previous and previous != alumni["id"]:
        await audit(request, "push.sub.reassign", actor="alumni:" + alumni["id"], subject="alumni:" + previous)
    return {"ok": True}


@api_view(body=EndpointBody, permission=require_alumni)
async def unsubscribe(request: HttpRequest):
    alumni = await require_alumni(request)
    body = parse_body(request, EndpointBody)
    await request.services.store.delete(
        "push_subs", filters={"endpoint": {"_eq": body.endpoint}, "alumni_id": {"_eq": alumni["id"]}}
    )
    return {"ok": True}
