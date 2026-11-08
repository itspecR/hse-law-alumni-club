async def acquire_lock(connection, key, *, wait=5):
    return await connection.lock(key, wait=wait)


async def release_lock(connection, key):
    return await connection.unlock(key)
