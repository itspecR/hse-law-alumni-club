import asyncio
import functools
import secrets

from django.contrib.auth.hashers import BCryptSHA256PasswordHasher


class ClubPasswordHasher(BCryptSHA256PasswordHasher):
    def hash(self, password):
        return self.encode(password, self.salt())


HASHER = ClubPasswordHasher()
PREFIX = "bcrypt_sha256$"
ROUNDS_MIN, ROUNDS_MAX = 4, 15


def password_slots():
    loop = asyncio.get_running_loop()
    if not hasattr(loop, "_club_password_slots"):
        loop._club_password_slots = asyncio.Semaphore(2)
    return loop._club_password_slots


async def hash_password(password):
    async with password_slots():
        return await password_work(HASHER.hash, password)


async def password_work(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


@functools.cache
def decoy():
    return HASHER.hash(secrets.token_urlsafe(16))


def usable(encoded):
    if not isinstance(encoded, str) or not encoded.startswith(PREFIX):
        return False
    try:
        return ROUNDS_MIN <= int(encoded.split("$")[3]) <= ROUNDS_MAX
    except ValueError, IndexError:
        return False


def checked(password, encoded):
    valid = usable(encoded)
    try:
        matched = HASHER.verify(password, encoded if valid else decoy())
    except ValueError, TypeError, IndexError:
        return False
    return valid and matched


async def verify_password(encoded, password):
    async with password_slots():
        return await password_work(checked, password, encoded)
