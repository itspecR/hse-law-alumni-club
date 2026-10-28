import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from django.http import HttpRequest
from psycopg.types.json import Jsonb

from club_api.core.errors import ApiError
from club_api.core.security import LoginAttempts, constant_equal
from club_api.db.queries import Query, acquire_lock
from club_api.db.store import Store
from club_api.modules.auth.passwords import hash_password, verify_password

logger = logging.getLogger("club.auth")
STAFF_ROLES = frozenset(("admin", "Administrator", "editor"))
FULL_ROLES = frozenset(("admin", "Administrator"))
DUPLICATE_ACCOUNT = "Аккаунт с этой почтой уже есть. Войдите или восстановите пароль."
ALUMNI_FIELDS = (
    "id",
    "user_id",
    "fio",
    "cohort",
    "verification_status",
    "personal_discount",
    "points_cached",
    "contacts_json",
    "edu_program",
    "edu_level",
    "interests_json",
    "podcast_sub_until",
    "avatar",
    "referral_code",
    "token_version",
    "telegram_id",
)


def local_account(user):
    return user and user.get("provider") in (None, "", "default") and (not user.get("tfa_secret"))


def recoverable_alumni(user):
    return local_account(user) and user["role_name"] == "alumni" and (user["status"] in ("active", "unverified"))


def public_user(user):
    return {
        **{
            key: str(user[key]) if key in ("id", "alumni_id") and user[key] is not None else user[key]
            for key in (
                "id",
                "email",
                "status",
                "first_name",
                "last_name",
                "staff_version",
                "alumni_id",
                "alumni_version",
            )
        },
        "role": user["role_name"] or "",
    }


async def find_user(connection, *, id=None, email=None, lock=False):
    if id is not None:
        try:
            UUID(id)
        except ValueError, TypeError, AttributeError:
            return None
    query = (
        "SELECT u.id,u.email,u.password,u.role,u.status,u.provider,u.tfa_secret,u.first_name,u.last_name,"
        "r.name AS role_name,COALESCE(s.token_version,0) AS staff_version,"
        "a.id AS alumni_id,COALESCE(a.token_version,0) AS alumni_version "
        "FROM directus_users u LEFT JOIN directus_roles r ON r.id=u.role "
        "LEFT JOIN club_staff_sessions s ON s.user_id=u.id LEFT JOIN alumni a ON a.user_id=u.id "
        "WHERE "
    )
    query += "u.id=%s" if id is not None else "lower(u.email)=%s"
    query += " LIMIT 2"
    if lock:
        query += " FOR UPDATE" if getattr(connection, "vendor", None) == "mysql" else " FOR UPDATE OF u"
    cursor = await connection.execute(query, (id if id is not None else email.lower().strip(),))
    rows = await cursor.fetchall()
    return rows[0] if len(rows) == 1 else None


class AuthService:
    def __init__(self, settings, database, store: Store):
        self.settings = settings
        self.database = database
        self.store = store
        self.attempts = LoginAttempts()
        self.confirmation_resends = {}

    def token(self, payload, ttl, *, admin=False):
        now = datetime.now(UTC)
        secret = (self.settings.secret("ADMIN_AUTH_SECRET") if admin else "") or self.settings.secret("AUTH_SECRET")
        return jwt.encode({**payload, "iat": now, "exp": now + timedelta(seconds=ttl)}, secret, algorithm="HS256")

    def decode(self, token, *, admin=False):
        secret = (self.settings.secret("ADMIN_AUTH_SECRET") if admin else "") or self.settings.secret("AUTH_SECRET")
        try:
            return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        except jwt.PyJWTError:
            return None

    def session(self, alumni_id, user_id, version=0):
        return self.token({"alumni_id": alumni_id, "sub": user_id, "ver": version}, 7 * 86400)

    def staff_session(self, user_id, role, version=0):
        return self.token(
            {"sub": user_id, "role": role, "scope": "admin", "jti": str(uuid4()), "ver": version}, 12 * 3600, admin=True
        )

    @staticmethod
    def bearer(request):
        match = re.fullmatch("Bearer\\s+(.+)", request.headers.get("authorization", ""), re.I)
        return match[1] if match else None

    def service_token(self, request):
        return constant_equal(self.bearer(request), self.settings.secret("POINTS_SERVICE_TOKEN"))

    async def user(self, *, id=None, email=None, alumni=False, active=False, staff=False):
        async with self.database.connection() as connection:
            user = await find_user(connection, id=id, email=email)
        if not user or (alumni and (not recoverable_alumni(user))):
            return None
        if staff and (not local_account(user) or user["role_name"] not in STAFF_ROLES):
            return None
        if active and user["status"] != "active":
            return None
        return public_user(user)

    async def authenticate(self, email, password, *, scope):
        async with self.database.connection() as connection:
            user = await find_user(connection, email=email)
        if (
            not local_account(user)
            or user["status"] not in ("active", "unverified")
            or (not await verify_password(user["password"], password))
        ):
            return ("invalid", None)
        allowed = user["role_name"] == "alumni" if scope == "alumni" else user["role_name"] in STAFF_ROLES
        if not allowed:
            return ("forbidden", None)
        if user["status"] == "unverified":
            return ("unverified" if scope == "alumni" else "invalid", None)
        return ("ok", public_user(user))

    async def profile(self, user_id):
        rows = await self.store.read("alumni", filters={"user_id": {"_eq": user_id}}, fields=ALUMNI_FIELDS, limit=1)
        return rows[0] if rows else None

    async def resolve_admin(self, request):
        token = self.bearer(request)
        if not token or self.service_token(request):
            return None
        payload = self.decode(token, admin=True)
        if not payload or payload.get("scope") != "admin":
            return None
        try:
            if payload.get("jti"):
                rows = await self.database.rows(
                    "SELECT token_key FROM club_auth_revocations WHERE token_key=%s AND expires_at > now()",
                    ("admin:" + payload["jti"],),
                )
                if rows:
                    return None
            user = await self.user(id=payload["sub"], staff=True, active=True)
            if not user or payload.get("ver", 0) != user["staff_version"]:
                return None
            return {"userId": user["id"], "role": user["role"], "jti": payload.get("jti")}
        except Exception:
            logger.warning("Не удалось проверить текущие права администратора")
            return None

    async def resolve_alumni(self, request):
        token = self.bearer(request)
        if not token or self.service_token(request):
            return None
        payload = self.decode(token)
        if not payload or not isinstance(payload.get("alumni_id"), str):
            return None
        try:
            UUID(payload["alumni_id"])
            rows = await self.store.read(
                "alumni", filters={"id": {"_eq": payload["alumni_id"]}}, fields=ALUMNI_FIELDS, limit=1
            )
            alumni = rows[0] if rows else None
            if not alumni or payload.get("ver", 0) != (alumni["token_version"] or 0):
                return None
            if alumni["user_id"]:
                if payload["sub"] != alumni["user_id"] or not await self.user(
                    id=alumni["user_id"], alumni=True, active=True
                ):
                    return None
            elif not alumni["telegram_id"] or payload["sub"] != alumni["telegram_id"]:
                return None
            return alumni
        except Exception:
            logger.warning("Не удалось проверить доступ выпускника")
            return None

    async def revoke_admin(self, jti):
        await self.database.execute(
            Query(
                "INSERT INTO club_auth_revocations(token_key,expires_at) VALUES(%s,now() + interval '12 hours') ON CONFLICT(token_key) DO UPDATE SET expires_at=EXCLUDED.expires_at",
                "INSERT INTO club_auth_revocations(token_key,expires_at) VALUES(%s,now() + INTERVAL 12 HOUR) ON DUPLICATE KEY UPDATE expires_at=VALUES(expires_at)",
            ),
            ("admin:" + jti,),
        )

    async def register(self, *, email, password, first_name, last_name, status, profile):
        hashed = await hash_password(password)
        async with self.database.transaction() as connection:
            await acquire_lock(connection, "auth-email:" + email)
            if await find_user(connection, email=email, lock=True):
                raise ApiError(409, DUPLICATE_ACCOUNT)
            cursor = await connection.execute("SELECT id FROM directus_roles WHERE name='alumni' LIMIT 2")
            roles = await cursor.fetchall()
            if len(roles) != 1:
                raise RuntimeError("Роль выпускника не настроена")
            user_id, profile_id = (str(uuid4()), str(uuid4()))
            await connection.execute(
                "INSERT INTO directus_users(id,email,password,role,status,provider,first_name,last_name) VALUES(%s,%s,%s,%s,%s,'default',%s,%s)",
                (user_id, email, hashed, roles[0]["id"], status, first_name, last_name),
            )
            await connection.execute(
                "INSERT INTO alumni(id,user_id,fio,cohort,edu_level,edu_program,interests_json,referral_code,referred_by,consent_at,consent_version,status,verification_status,points_cached,level_cached,personal_discount,token_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active','pending',0,'graduate',0,0)",
                (
                    profile_id,
                    user_id,
                    profile["fio"],
                    profile["cohort"],
                    profile["edu_level"],
                    profile["edu_program"],
                    Jsonb(profile["interests_json"]),
                    profile["referral_code"],
                    profile["referred_by"],
                    profile["consent_at"],
                    profile["consent_version"],
                ),
            )
        return user_id

    async def confirm(self, user_id):
        async with self.database.transaction() as connection:
            user = await find_user(connection, id=user_id, lock=True)
            if not recoverable_alumni(user):
                return "invalid"
            if user["status"] == "active":
                return "already"
            await connection.execute("UPDATE directus_users SET status='active' WHERE id=%s", (user_id,))
            return "confirmed"

    async def reset(self, user_id, password, jti, version):
        if not jti:
            return "invalid"
        hashed = await hash_password(password)
        async with self.database.transaction() as connection:
            user = await find_user(connection, id=user_id, lock=True)
            if not recoverable_alumni(user):
                return "invalid"
            cursor = await connection.execute(
                "SELECT id,token_version FROM alumni WHERE user_id=%s ORDER BY id LIMIT 2 FOR UPDATE", (user_id,)
            )
            profiles = await cursor.fetchall()
            if len(profiles) != 1:
                return "invalid"
            profile = profiles[0]
            if version is not None and version != (profile["token_version"] or 0):
                return "used"
            await acquire_lock(connection, "reset:" + jti)
            previous = await (
                await connection.execute(
                    "SELECT token_key FROM club_auth_revocations WHERE token_key=%s", ("reset:" + jti,)
                )
            ).fetchone()
            if previous:
                return "used"
            await connection.execute(
                Query(
                    "INSERT INTO club_auth_revocations(token_key,expires_at) VALUES(%s,now() + interval '24 hours')",
                    "INSERT INTO club_auth_revocations(token_key,expires_at) VALUES(%s,now() + INTERVAL 24 HOUR)",
                ),
                ("reset:" + jti,),
            )
            await connection.execute("UPDATE directus_users SET password=%s WHERE id=%s", (hashed, user_id))
            await connection.execute(
                "UPDATE alumni SET token_version=COALESCE(token_version,0)+1 WHERE id=%s", (profile["id"],)
            )
            return "reset"


def auth_service(request: HttpRequest) -> AuthService:
    return request.services.auth


async def require_admin(request: HttpRequest):
    admin = await auth_service(request).resolve_admin(request)
    if not admin:
        raise ApiError(401, "Требуется вход администратора")
    return admin


async def require_full_admin(request: HttpRequest):
    admin = await require_admin(request)
    if admin["role"] not in FULL_ROLES:
        raise ApiError(403, "Операция доступна только администратору клуба")
    return admin


async def require_alumni(request: HttpRequest):
    alumni = await auth_service(request).resolve_alumni(request)
    if not alumni:
        raise ApiError(401, "Войдите в кабинет.")
    return alumni
