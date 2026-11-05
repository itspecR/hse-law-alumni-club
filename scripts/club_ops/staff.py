import re
from datetime import UTC, datetime
from uuid import uuid4

from club_api.db.operator import is_operator
from club_api.db.queries import Query, acquire_lock
from club_api.modules.auth.codes import ADMIN_PURPOSE, issue_code
from club_api.modules.auth.passwords import hash_password
from club_ops.bootstrap import OperatorError, email

STAFF_ROLES = ("admin", "Administrator", "editor")
SECRET_MIN_LENGTH = 32
STAFF_SQL = (
    "SELECT u.id,r.name AS role,u.status,u.provider,u.tfa_secret FROM directus_users u "
    "LEFT JOIN directus_roles r ON r.id=u.role WHERE lower(u.email)=%s LIMIT 2"
)


def validate_input(action, address, role, password):
    if action not in ("create", "reset-password"):
        raise OperatorError("STAFF_ACTION должен быть create или reset-password")
    if role not in ("admin", "editor"):
        raise OperatorError("STAFF_ROLE должен быть admin или editor")
    address = email(address, "STAFF_EMAIL")
    if not password or not 12 <= len(password) <= 100 or re.search(r"[\r\n]", password):
        raise OperatorError("Пароль сотрудника должен содержать 12–100 символов без переноса строки")
    return action, address, role, password


async def manage_staff(connection, *, action, address, role, password):
    action, address, role, password = validate_input(action, address, role, password)
    async with connection.transaction():
        if not await is_operator(connection):
            raise OperatorError("Команда доступна только владельцу базы данных")
        await acquire_lock(connection, "auth-email:" + address)
        rows = await (
            await connection.execute(
                Query(
                    "SELECT u.id,r.name AS role,u.status,u.provider,u.tfa_secret FROM directus_users u LEFT JOIN directus_roles r ON r.id=u.role WHERE lower(u.email)=%s LIMIT 2 FOR UPDATE OF u",
                    "SELECT u.id,r.name AS role,u.status,u.provider,u.tfa_secret FROM directus_users u LEFT JOIN directus_roles r ON r.id=u.role WHERE lower(u.email)=%s LIMIT 2 FOR UPDATE",
                ),
                (address,),
            )
        ).fetchall()
        if len(rows) > 1:
            raise OperatorError("Адрес неоднозначен; исправьте дубликаты перед операцией")
        if action == "create":
            if rows:
                raise OperatorError("Аккаунт уже существует; создание не меняет пароль или роль")
            roles = await (
                await connection.execute(
                    Query(
                        "SELECT id FROM directus_roles WHERE name=ANY(%s::text[]) LIMIT 2",
                        "SELECT id FROM directus_roles WHERE name IN (SELECT value FROM JSON_TABLE(%s,'$[*]' COLUMNS(value VARCHAR(255) PATH '$')) names) LIMIT 2",
                    ),
                    (["admin", "Administrator"] if role == "admin" else ["editor"],),
                )
            ).fetchall()
            if len(roles) != 1:
                raise OperatorError("Нужная роль отсутствует или неоднозначна; выполните bootstrap")
            await connection.execute(
                "INSERT INTO directus_users(id,email,password,role,status,provider,first_name,last_name) VALUES(%s,%s,%s,%s,'active','default','Сотрудник','')",
                (str(uuid4()), address, await hash_password(password), roles[0]["id"]),
            )
        else:
            user = rows[0] if rows else None
            if not user or user["role"] not in (("admin", "Administrator") if role == "admin" else ("editor",)):
                raise OperatorError("Существующий аккаунт должен иметь указанную роль сотрудника; повышения прав нет")
            if user["status"] != "active" or user["provider"] not in (None, "", "default") or user["tfa_secret"]:
                raise OperatorError(
                    "Сброс доступен активному локальному аккаунту без MFA; статус и способ входа не меняются"
                )
            await connection.execute(
                "UPDATE directus_users SET password=%s WHERE id=%s", (await hash_password(password), user["id"])
            )
            await connection.execute(
                Query(
                    "INSERT INTO club_staff_sessions(user_id,token_version) VALUES(%s,1) ON CONFLICT(user_id) DO UPDATE SET token_version=club_staff_sessions.token_version+1",
                    "INSERT INTO club_staff_sessions(user_id,token_version) VALUES(%s,1) ON DUPLICATE KEY UPDATE token_version=club_staff_sessions.token_version+1",
                ),
                (user["id"],),
            )
    return "created" if action == "create" else "password-reset"


def active_staff(rows):
    user = rows[0] if len(rows) == 1 else None
    return bool(
        user
        and user["role"] in STAFF_ROLES
        and user["status"] == "active"
        and user["provider"] in (None, "", "default")
        and not user["tfa_secret"]
    )


async def staff_code(connection, *, address, secret):
    address = email(address, "STAFF_EMAIL")
    if len(secret or "") < SECRET_MIN_LENGTH:
        raise OperatorError("Нужен AUTH_SECRET сайта из закрытого файла настроек оператора")
    async with connection.transaction():
        if not await is_operator(connection):
            raise OperatorError("Команда доступна только владельцу базы данных")
        rows = await (await connection.execute(STAFF_SQL, (address,))).fetchall()
        if not active_staff(rows):
            raise OperatorError("Нужен активный локальный аккаунт сотрудника без MFA")
        return await issue_code(connection, secret, str(rows[0]["id"]), ADMIN_PURPOSE, datetime.now(UTC), limited=False)
