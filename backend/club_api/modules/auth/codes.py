import hashlib
import hmac
import secrets
from datetime import timedelta

from club_api.core.models import parse_date
from club_api.db.queries import acquire_lock

CODE_DIGITS = 6
CODE_MAX_ATTEMPTS = 5
CODE_RESEND = timedelta(seconds=60)
CODE_WINDOW = timedelta(hours=1)
CODE_WINDOW_LIMIT = 5
CONFIRM_PURPOSE = "email-confirm"
RESET_PURPOSE = "reset"
ADMIN_PURPOSE = "admin-login"
MEMBER_PURPOSE = "member-login"
CODE_TTL = {
    CONFIRM_PURPOSE: timedelta(minutes=15),
    RESET_PURPOSE: timedelta(minutes=15),
    ADMIN_PURPOSE: timedelta(minutes=10),
    MEMBER_PURPOSE: timedelta(minutes=10),
}
STORED_SQL = (
    "SELECT id,code_hash,attempts,expires_at,sent_at,window_started_at,window_count "
    "FROM club_email_codes WHERE user_id=%s AND purpose=%s FOR UPDATE"
)


def ttl_minutes(purpose):
    return int(CODE_TTL[purpose].total_seconds() // 60)


def new_code():
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def code_hash(secret, user_id, purpose, code):
    return hmac.new(secret.encode(), f"{purpose}:{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


def clean_code(value):
    return "".join(character for character in value or "" if character.isdigit())


async def stored(connection, user_id, purpose):
    cursor = await connection.execute(STORED_SQL, (user_id, purpose))
    return await cursor.fetchone()


def window(row, now):
    if row and parse_date(row["window_started_at"]) + CODE_WINDOW > now:
        return parse_date(row["window_started_at"]), row["window_count"]
    return now, 0


def waiting(row, now):
    return bool(row["code_hash"]) and parse_date(row["sent_at"]) + CODE_RESEND > now


def can_send(row, now):
    if not row:
        return True
    _, count = window(row, now)
    return not waiting(row, now) and count < CODE_WINDOW_LIMIT


async def issue_code(connection, secret, user_id, purpose, now, *, limited=True):
    await acquire_lock(connection, f"code:{purpose}:{user_id}")
    row = await stored(connection, user_id, purpose)
    if limited and not can_send(row, now):
        return None
    code = new_code()
    started, count = window(row, now)
    values = (code_hash(secret, user_id, purpose, code), now + CODE_TTL[purpose], now, started, count + 1)
    if row:
        await connection.execute(
            "UPDATE club_email_codes SET code_hash=%s,attempts=0,expires_at=%s,sent_at=%s,"
            "window_started_at=%s,window_count=%s WHERE id=%s",
            (*values, row["id"]),
        )
    else:
        await connection.execute(
            "INSERT INTO club_email_codes(code_hash,attempts,expires_at,sent_at,window_started_at,window_count,"
            "user_id,purpose) VALUES(%s,0,%s,%s,%s,%s,%s,%s)",
            (*values, user_id, purpose),
        )
    return code


def usable(row, now):
    return bool(
        row and row["code_hash"] and row["attempts"] < CODE_MAX_ATTEMPTS and parse_date(row["expires_at"]) > now
    )


async def accept_code(connection, secret, user_id, purpose, code, now):
    row = await stored(connection, user_id, purpose)
    if not usable(row, now):
        return False
    if hmac.compare_digest(row["code_hash"], code_hash(secret, user_id, purpose, clean_code(code))):
        await connection.execute("UPDATE club_email_codes SET code_hash=NULL WHERE id=%s", (row["id"],))
        return True
    await connection.execute("UPDATE club_email_codes SET attempts=attempts+1 WHERE id=%s", (row["id"],))
    return False


async def forget_codes(connection, user_id):
    await connection.execute("DELETE FROM club_email_codes WHERE user_id=%s", (user_id,))
