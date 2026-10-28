import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime

from django.http import HttpRequest
from pydantic import Field

from club_api.core.errors import ApiError
from club_api.core.views import api_view
from club_api.db.queries import Query
from club_api.modules.auth.routes import Body

CAPTCHA_MAX_NUMBER = 50000
CAPTCHA_TTL_SECONDS = 600
CAPTCHA_ALGORITHM = "SHA-256"
SALT_PATTERN = r"^[0-9a-f]{32}\.[0-9]{10}$"
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
USE_SQL = Query(
    "INSERT INTO club_captcha_uses(salt_hash,expires_at) VALUES(%s,%s) ON CONFLICT(salt_hash) DO NOTHING",
    "INSERT IGNORE INTO club_captcha_uses(salt_hash,expires_at) VALUES(%s,%s)",
)
EXPIRED_SQL = "DELETE FROM club_captcha_uses WHERE expires_at<%s"


class CaptchaBody(Body):
    salt: str = Field(pattern=SALT_PATTERN)
    number: int = Field(ge=0, le=CAPTCHA_MAX_NUMBER)
    signature: str = Field(pattern=DIGEST_PATTERN)


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def signature(settings, challenge):
    key = settings.secret("AUTH_SECRET").encode()
    return hmac.new(key, ("captcha." + challenge).encode(), hashlib.sha256).hexdigest()


def new_challenge(settings, now):
    salt = f"{secrets.token_hex(16)}.{int(now) + CAPTCHA_TTL_SECONDS}"
    challenge = sha256(salt + str(secrets.randbelow(CAPTCHA_MAX_NUMBER + 1)))
    return {
        "algorithm": CAPTCHA_ALGORITHM,
        "salt": salt,
        "challenge": challenge,
        "maxnumber": CAPTCHA_MAX_NUMBER,
        "signature": signature(settings, challenge),
    }


def expiry(captcha):
    return int(captcha.salt.rsplit(".", 1)[1])


def require_solution(settings, captcha, now):
    if captcha is None:
        raise ApiError(400, "Проверка не пройдена: обновите страницу и отправьте заявку ещё раз")
    if expiry(captcha) < now:
        raise ApiError(400, "Проверка устарела: отправьте заявку ещё раз")
    expected = signature(settings, sha256(captcha.salt + str(captcha.number)))
    if not hmac.compare_digest(expected, captcha.signature):
        raise ApiError(400, "Проверка не пройдена: обновите страницу и отправьте заявку ещё раз")


async def require_captcha(state, captcha):
    now = time.time()
    require_solution(state.settings, captcha, now)
    used = datetime.fromtimestamp(expiry(captcha), UTC)
    if await state.database.execute(USE_SQL, (sha256(captcha.salt), used)) != 1:
        raise ApiError(400, "Проверка уже использована: отправьте заявку ещё раз")


async def forget_expired(state):
    await state.database.execute(EXPIRED_SQL, (datetime.now(UTC),))


@api_view
async def challenge(request: HttpRequest):
    return new_challenge(request.services.settings, time.time())
