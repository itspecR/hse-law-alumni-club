import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qsl

INIT_DATA_MAX_AGE = 60 * 60


def validate_init_data(data, token, *, max_age=INIT_DATA_MAX_AGE, now=None):
    fields = parse_qsl(data, keep_blank_values=True)
    if len({key for key, _ in fields}) != len(fields):
        return None
    values = dict(fields)
    signature = values.pop("hash", "")
    if not re.fullmatch(r"[a-f0-9]{64}", signature, re.I):
        return None
    message = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        return None
    now = time.time() if now is None else now
    try:
        issued = int(values.get("auth_date", "0"))
        if issued <= 0 or issued > now + 60 or now - issued > max_age:
            return None
        user = json.loads(values.get("user", "null"))
        return user if isinstance(user, dict) else None
    except ValueError, TypeError:
        return None
