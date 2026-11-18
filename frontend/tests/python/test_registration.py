from club_web import registration
from club_web.client import ApiFailure


class FakeApi:
    def __init__(self, replies):
        self.client = object.__new__(type("Client", (), {}))
        self.replies = list(replies)
        self.calls = 0

    async def get(self, path):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


async def test_status_is_remembered_for_a_minute(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(registration.time, "monotonic", lambda: now[0])
    api = FakeApi([{"enabled": True}, {"enabled": False}])
    assert await registration.registration_open(api) is True
    now[0] += registration.STATUS_TTL_SECONDS - 1
    assert await registration.registration_open(api) is True and api.calls == 1
    now[0] += 2
    assert await registration.registration_open(api) is False and api.calls == 2


async def test_failure_is_not_remembered(monkeypatch):
    monkeypatch.setattr(registration.time, "monotonic", lambda: 2000.0)
    api = FakeApi([ApiFailure(503, "down"), {"enabled": True}])
    assert await registration.registration_open(api) is False
    assert await registration.registration_open(api) is True and api.calls == 2
