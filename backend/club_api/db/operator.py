from django.db import DatabaseError


async def is_operator(connection):
    try:
        await connection.execute("SELECT `key` FROM club_settings LIMIT 0")
        return True
    except DatabaseError:
        return False
