class Query(str):
    def __new__(cls, postgresql, mariadb):
        value = super().__new__(cls, postgresql)
        value.mariadb = mariadb
        return value

    def __add__(self, other):
        return Query(str(self) + str(other), self.mariadb + getattr(other, "mariadb", other))

    def __radd__(self, other):
        return Query(str(other) + str(self), getattr(other, "mariadb", other) + self.mariadb)


async def acquire_lock(connection, key, *, wait=5):
    return await connection.lock(key, wait=wait)


async def release_lock(connection, key):
    return await connection.unlock(key)
