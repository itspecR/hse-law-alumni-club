import logging

from club_api.core.errors import ApiError
from club_api.db.queries import Query, acquire_lock
from club_api.modules.notifications.mail import EMAIL_CONFIRMATION_KIND, mail_user_lock

logger = logging.getLogger("club.members")
DELETED_NAME = "Удаленный участник"


async def alumni_email(state, id):
    rows = await state.database.rows(
        Query(
            "SELECT u.email,a.contacts_json->>'email' AS contact_email FROM alumni a LEFT JOIN directus_users u ON u.id=a.user_id WHERE a.id=%s",
            "SELECT u.email,JSON_UNQUOTE(JSON_EXTRACT(a.contacts_json,'$.email')) AS contact_email FROM alumni a LEFT JOIN directus_users u ON u.id=a.user_id WHERE a.id=%s",
        ),
        (id,),
    )
    return (rows[0]["email"] or rows[0]["contact_email"]) if rows else None


async def anonymize(state, alumni_id):
    profiles = await state.database.rows("SELECT user_id FROM alumni WHERE id=%s", (alumni_id,))
    if not profiles:
        return False
    user_id = profiles[0]["user_id"]
    async with state.database.transaction() as connection:
        user = None
        if user_id:
            await acquire_lock(connection, mail_user_lock(user_id))
            user = await (
                await connection.execute(
                    Query(
                        "SELECT u.email,r.name AS role_name FROM directus_users u LEFT JOIN directus_roles r ON r.id=u.role WHERE u.id=%s FOR UPDATE OF u",
                        "SELECT u.email,r.name AS role_name FROM directus_users u LEFT JOIN directus_roles r ON r.id=u.role WHERE u.id=%s FOR UPDATE",
                    ),
                    (user_id,),
                )
            ).fetchone()
        cursor = await connection.execute("SELECT id,user_id,avatar FROM alumni WHERE id=%s FOR UPDATE", (alumni_id,))
        alumni = await cursor.fetchone()
        if not alumni:
            return False
        if alumni["user_id"] != user_id:
            raise ApiError(503, "Данные сейчас изменяются. Повторите позже.")
        if user_id:
            await connection.execute(
                "DELETE FROM club_mail_outbox WHERE kind=%s AND owner_user_id=%s",
                (EMAIL_CONFIRMATION_KIND, user_id),
            )
            if user and user["role_name"] == "alumni" and user["email"]:
                await connection.execute(
                    "DELETE FROM club_mail_outbox WHERE kind=%s AND owner_user_id IS NULL AND lower(to_addr)=%s",
                    (EMAIL_CONFIRMATION_KIND, user["email"].lower().strip()),
                )
        await connection.execute("DELETE FROM club_social_reactions WHERE alumni_id=%s", (alumni_id,))
        await connection.execute("DELETE FROM club_social_membership WHERE alumni_id=%s", (alumni_id,))
        await connection.execute(
            "UPDATE alumni SET fio=%s,contacts_json=NULL,telegram_id=NULL,avatar=NULL,interests_json=NULL,edu_program=NULL,edu_level=NULL,cohort=NULL,verification_status='rejected',points_cached=0,status='alumni_left',user_id=NULL,token_version=COALESCE(token_version,0)+1 WHERE id=%s",
            (DELETED_NAME, alumni_id),
        )
        await connection.execute(
            "UPDATE orders SET contact_fio=%s,contact_phone='-',contact_email='-',address=NULL,comment=NULL WHERE alumni_id=%s",
            (DELETED_NAME, alumni_id),
        )
        await connection.execute(
            "DELETE FROM alumni_friends WHERE alumni_id=%s OR friend_id=%s", (alumni_id, alumni_id)
        )
        await connection.execute("DELETE FROM push_subs WHERE alumni_id=%s", (alumni_id,))
        if alumni["user_id"]:
            await connection.execute(
                "UPDATE directus_users SET email=%s,first_name='Удален',last_name='-',status='suspended' WHERE id=%s AND role IN (SELECT id FROM directus_roles WHERE name='alumni')",
                (f"deleted-{alumni['user_id']}@invalid.local", alumni["user_id"]),
            )
            await connection.execute(
                "DELETE FROM directus_users WHERE id=%s AND role IN (SELECT id FROM directus_roles WHERE name='alumni')",
                (alumni["user_id"],),
            )
    if alumni["avatar"]:
        try:
            await state.media.delete(str(alumni["avatar"]))
        except Exception:
            logger.error("Не удалось удалить прежний файл профиля")
    return True
