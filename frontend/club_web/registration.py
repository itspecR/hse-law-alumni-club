import time
from weakref import WeakKeyDictionary

from club_web.client import ApiFailure

STATUS_PATH = "/auth/registration"
STATUS_TTL_SECONDS = 60
checked = WeakKeyDictionary()


def remembered(client, now):
    entry = checked.get(client)
    return entry if entry and now - entry[1] < STATUS_TTL_SECONDS else None


async def registration_open(api):
    now = time.monotonic()
    entry = remembered(api.client, now)
    if entry:
        return entry[0]
    try:
        reply = await api.get(STATUS_PATH)
    except ApiFailure:
        return False
    opened = isinstance(reply, dict) and reply.get("enabled") is True
    checked[api.client] = (opened, now)
    return opened
