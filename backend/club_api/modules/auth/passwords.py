import asyncio

from django.contrib.auth.hashers import BCryptSHA256PasswordHasher


class ClubPasswordHasher(BCryptSHA256PasswordHasher):
    def hash(self, password):
        return self.encode(password, self.salt())


HASHER = ClubPasswordHasher()


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


async def verify_password(encoded, password):
    if not isinstance(encoded, str) or not encoded.startswith("bcrypt_sha256$"):
        return False
    try:
        if not 4 <= int(encoded.split("$")[3]) <= 15:
            return False
        async with password_slots():
            return await password_work(HASHER.verify, password, encoded)
    except ValueError, TypeError, IndexError:
        return False
