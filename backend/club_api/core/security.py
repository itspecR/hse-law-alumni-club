import hashlib
import hmac
import ipaddress
import time
from collections import defaultdict, deque

PROXY_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
YOOKASSA_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "185.71.76.0/27",
        "185.71.77.0/27",
        "77.75.153.0/25",
        "77.75.154.128/25",
        "77.75.156.11/32",
        "77.75.156.35/32",
        "2a02:5180::/32",
    )
)


def ip_value(address):
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        return None
    return getattr(value, "ipv4_mapped", None) or value


def ipv4(address):
    value = ip_value(address)
    return value if value is not None and value.version == 4 else None


def trust_proxy(address, hop=0):
    value = ipv4(address)
    return hop == 0 and value is not None and any(value in network for network in PROXY_NETWORKS)


def client_ip(request):
    peer = request.META.get("REMOTE_ADDR", "")
    if trust_proxy(peer):
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


def yookassa_ip(address):
    value = ip_value(address)
    return value is not None and any(value in network for network in YOOKASSA_NETWORKS)


def constant_equal(supplied, expected):
    return bool(supplied and expected) and hmac.compare_digest(
        hashlib.sha256(supplied.encode()).digest(), hashlib.sha256(expected.encode()).digest()
    )


class LoginAttempts:
    WINDOW_SECONDS = 15 * 60
    ACCOUNT_LIMIT, ACCOUNT_LOCK_SECONDS = 10, 15 * 60
    ADDRESS_LIMIT, ADDRESS_LOCK_SECONDS = 30, 30 * 60
    GUARD_LIMIT, GUARD_WINDOW_SECONDS, GUARD_SECONDS = 20, 60 * 60, 60 * 60

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.account = {}
        self.ip = {}
        self.email = {}

    def locks(self, email, ip):
        return (
            (self.account, (email.lower(), ip), self.ACCOUNT_LIMIT, self.ACCOUNT_LOCK_SECONDS, self.WINDOW_SECONDS),
            (self.ip, ip, self.ADDRESS_LIMIT, self.ADDRESS_LOCK_SECONDS, self.WINDOW_SECONDS),
        )

    def counters(self, email, ip):
        guard = (self.email, email.lower(), self.GUARD_LIMIT, self.GUARD_SECONDS, self.GUARD_WINDOW_SECONDS)
        return (*self.locks(email, ip), guard)

    def cleanup(self):
        now = self.clock()
        for entries, window in (
            (self.account, self.WINDOW_SECONDS),
            (self.ip, self.WINDOW_SECONDS),
            (self.email, self.GUARD_WINDOW_SECONDS),
        ):
            for key, (_, first, until) in list(entries.items()):
                if until < now and now - first > window:
                    del entries[key]

    def locked(self, email, ip):
        self.cleanup()
        now = self.clock()
        return any(entries.get(key, (0, 0, 0))[2] > now for entries, key, *_ in self.locks(email, ip))

    def guarded(self, email):
        return self.email.get(email.lower(), (0, 0, 0))[2] > self.clock()

    def fail(self, email, ip):
        now = self.clock()
        for entries, key, maximum, duration, window in self.counters(email, ip):
            fails, first, until = entries.get(key, (0, now, 0))
            if now - first > window:
                fails, first = 0, now
            fails += 1
            entries[key] = fails, first, now + duration if fails >= maximum or until > now else until

    def success(self, email, ip):
        self.account.pop((email.lower(), ip), None)


class RateLimits:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.entries = defaultdict(deque)
        self.last_cleanup = clock()

    def check(self, key, maximum, window=60):
        now = self.clock()
        if now - self.last_cleanup >= 60:
            for item, attempts in list(self.entries.items()):
                if not attempts or now - attempts[-1] >= 600:
                    del self.entries[item]
            self.last_cleanup = now
        attempts = self.entries[key]
        while attempts and attempts[0] <= now - window:
            attempts.popleft()
        allowed = len(attempts) < maximum
        if len(attempts) < maximum:
            attempts.append(now)
        elif attempts:
            attempts[-1] = now
        return allowed
