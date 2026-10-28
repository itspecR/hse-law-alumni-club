import json
import re
from dataclasses import dataclass
from importlib.resources import files
from uuid import uuid4

from psycopg.types.json import Jsonb

from club_api.db.operator import is_operator
from club_api.db.queries import Query, acquire_lock
from club_api.db.store import Store
from club_api.domain import DOMAIN
from club_api.modules.auth.passwords import hash_password

HOME_HERO = {
    "badge": "Клуб выпускников факультета права",
    "title_pre": "Свои люди",
    "title_accent": "после выпуска.",
    "subtitle": "Клуб выпускников факультета права Вышки",
    "cta_primary": "Вступить в клуб",
    "cta_secondary": "Как вступить",
}
HOME_CTA = {
    "title": "Три шага, и вы в клубе.",
    "text": "Проверка обычно занимает 1-2 рабочих дня.",
    "button": "Вступить в клуб",
}


class OperatorError(Exception):
    pass


def email(value, field):
    value = (value or "").strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) or len(value) > 128:
        raise OperatorError("Некорректный адрес в " + field)
    return value


@dataclass
class BootstrapConfig:
    app_env: str
    seed_demo: bool
    admin_email: str
    admin_password: str
    public_url: str
    editor_email: str = ""
    editor_password: str = ""
    alumni_email: str = ""
    alumni_password: str = ""

    def validate(self):
        if self.app_env not in ("development", "production"):
            raise OperatorError("Некорректный APP_ENV")
        if self.app_env == "production" and self.seed_demo:
            raise OperatorError("SEED_DEMO=true запрещён при APP_ENV=production")
        if (
            not self.admin_password
            or self.app_env == "production"
            and (
                len(self.admin_password) < 12
                or re.search(r"replace_with|changeme|сгенерируйте", self.admin_password, re.I)
            )
        ):
            raise OperatorError("ADMIN_PASSWORD не задан или не подходит для production")
        self.admin_email = email(self.admin_email, "ADMIN_EMAIL")
        if self.seed_demo:
            if not all((self.editor_email, self.editor_password, self.alumni_email, self.alumni_password)):
                raise OperatorError("Для демо нужны TEST_EDITOR_EMAIL/PASSWORD и TEST_ALUMNI_EMAIL/PASSWORD")
            self.editor_email = email(self.editor_email, "TEST_EDITOR_EMAIL")
            self.alumni_email = email(self.alumni_email, "TEST_ALUMNI_EMAIL")
            if len({self.admin_email, self.editor_email, self.alumni_email}) != 3:
                raise OperatorError("Адреса администратора и демо-аккаунтов должны различаться")

    @classmethod
    def from_env(cls, values):
        if values.get("SEED_DEMO", "false") not in ("true", "false"):
            raise OperatorError("SEED_DEMO должен быть true или false")
        config = cls(
            values.get("APP_ENV", "development"),
            values.get("SEED_DEMO") == "true",
            values.get("ADMIN_EMAIL", ""),
            values.get("ADMIN_PASSWORD", ""),
            values.get("PUBLIC_URL", "http://localhost"),
            values.get("TEST_EDITOR_EMAIL", ""),
            values.get("TEST_EDITOR_PASSWORD", ""),
            values.get("TEST_ALUMNI_EMAIL", ""),
            values.get("TEST_ALUMNI_PASSWORD", ""),
        )
        config.validate()
        return config


async def ensure_role(connection, name, aliases=()):
    rows = await (
        await connection.execute(
            Query(
                "SELECT id FROM directus_roles WHERE name=ANY(%s::text[]) ORDER BY name",
                "SELECT id FROM directus_roles WHERE name IN (SELECT value FROM JSON_TABLE(%s,'$[*]' COLUMNS(value VARCHAR(255) PATH '$')) names) ORDER BY name",
            ),
            ([name, *aliases],),
        )
    ).fetchall()
    if len(rows) > 1:
        raise OperatorError("Неоднозначная роль " + name + ": требуется явное сопоставление")
    if rows:
        return str(rows[0]["id"])
    id = str(uuid4())
    await connection.execute("INSERT INTO directus_roles(id,name) VALUES(%s,%s)", (id, name))
    return id


async def ensure_user(connection, address, password, role_id, roles, first_name, last_name):
    address = email(address, "bootstrap email")
    rows = await (
        await connection.execute(
            "SELECT u.id,u.status,r.name AS role_name FROM directus_users u LEFT JOIN directus_roles r ON r.id=u.role WHERE lower(u.email)=%s",
            (address,),
        )
    ).fetchall()
    if len(rows) > 1:
        raise OperatorError("Адрес bootstrap неоднозначен: требуется проверка дубликатов")
    if rows:
        if rows[0]["status"] != "active" or rows[0]["role_name"] not in roles:
            raise OperatorError("Существующий аккаунт bootstrap заблокирован или имеет другую роль; права не изменены")
        return str(rows[0]["id"]), False
    id = str(uuid4())
    await connection.execute(
        "INSERT INTO directus_users(id,email,password,role,status,provider,first_name,last_name) VALUES(%s,%s,%s,%s,'active','default',%s,%s)",
        (id, address, await hash_password(password), role_id, first_name, last_name),
    )
    return id, True


async def seed_missing(connection, store, table, key, rows):
    have = {row[key] for row in await store.read(table, fields=(key,), limit=-1, connection=connection)}
    created = 0
    for row in rows:
        if row[key] not in have:
            await store.create(table, row, connection=connection)
            have.add(row[key])
            created += 1
    return created


async def ensure_site(connection, public_url):
    rows = await (
        await connection.execute(
            'SELECT value FROM club_settings WHERE "key" LIKE \'legacy_directus:%%\' ORDER BY "key" LIMIT 1'
        )
    ).fetchall()
    old = rows[0]["value"] if rows else {}

    def existing(field, default=None):
        value = old.get(field)
        return value if isinstance(value, str) and value.strip() else default

    site = {
        "title": existing("project_name")
        if existing("project_name") not in (None, "Directus")
        else "Клуб выпускников факультета права Вышки",
        "description": existing(
            "project_descriptor",
            "Клуб выпускников факультета права Вышки: программы ДПО, мерч, подкасты и кабинет участника.",
        ),
        "url": existing("project_url", public_url),
        "language": existing("default_language", "ru-RU"),
        "logo": existing("project_logo"),
        "favicon": existing("public_favicon"),
    }
    await connection.execute(
        Query(
            'INSERT INTO club_settings("key",value) VALUES(\'site\',%s) ON CONFLICT("key") DO NOTHING',
            "INSERT INTO club_settings(`key`,value) VALUES('site',%s) ON DUPLICATE KEY UPDATE `key`=`key`",
        ),
        (Jsonb(site),),
    )


async def bootstrap(connection, config):
    config.validate()
    store = Store(connection) if getattr(connection, "vendor", None) == "mysql" else Store(None)
    async with connection.transaction():
        if not await is_operator(connection):
            raise OperatorError("Bootstrap доступен только владельцу базы данных")
        if getattr(connection, "vendor", None) != "mysql":
            await connection.execute("SET LOCAL lock_timeout='30s'")
        await acquire_lock(connection, "club:native-bootstrap:v1")
        admin_role = await ensure_role(connection, "admin", ("Administrator",))
        alumni_role, editor_role = await ensure_role(connection, "alumni"), await ensure_role(connection, "editor")
        _, created = await ensure_user(
            connection,
            config.admin_email,
            config.admin_password,
            admin_role,
            ("admin", "Administrator"),
            "Администратор",
            "Клуба",
        )
        result = {"createdUsers": int(created), "createdRows": 0, "createdHome": False}
        for table, key, rows in (
            ("levels", "key", [{**row, "color": ""} for row in DOMAIN["levels"]]),
            ("point_rules", "reason", [{**row, "active": True} for row in DOMAIN["point_rules"]]),
            (
                "achievements",
                "key",
                [{key: value for key, value in row.items() if key != "star"} for row in DOMAIN["achievements"]],
            ),
        ):
            result["createdRows"] += await seed_missing(connection, store, table, key, rows)
        pages = await store.read(
            "pages", filters={"slug": {"_eq": "home"}}, fields=("id",), limit=1, connection=connection
        )
        if not pages:
            home = await store.create(
                "pages", {"slug": "home", "title": "Главная", "status": "published"}, connection=connection
            )
            for index, (table, data) in enumerate((("block_hero", HOME_HERO), ("block_cta", HOME_CTA)), 1):
                block = await store.create(table, data, connection=connection)
                await store.create(
                    "pages_blocks",
                    {"pages_id": home["id"], "collection": table, "item": block["id"], "sort": index},
                    connection=connection,
                )
            result["createdHome"] = True
        await ensure_site(connection, config.public_url)
        if config.seed_demo:
            _, editor_created = await ensure_user(
                connection, config.editor_email, config.editor_password, editor_role, ("editor",), "Тест", "Офис"
            )
            user_id, alumni_created = await ensure_user(
                connection,
                config.alumni_email,
                config.alumni_password,
                alumni_role,
                ("alumni",),
                "Сергей",
                "Кондратьев",
            )
            result["createdUsers"] += int(editor_created) + int(alumni_created)
            demo = json.loads(files("club_ops").joinpath("data/demo.json").read_text(encoding="utf-8"))
            from club_ops.catalog import read_catalog

            demo["programs"] = [{**row, "status": "published"} for row in read_catalog()]
            for table, rows in demo.items():
                key = (
                    "slug"
                    if table in ("programs", "products", "news")
                    else "referral_code"
                    if table == "alumni"
                    else "title"
                )
                result["createdRows"] += await seed_missing(connection, store, table, key, rows)
            if not await store.read(
                "alumni", filters={"user_id": {"_eq": user_id}}, fields=("id",), limit=1, connection=connection
            ):
                await store.create(
                    "alumni",
                    {
                        "user_id": user_id,
                        "fio": "Сергей Кондратьев",
                        "cohort": "2026",
                        "edu_program": "Публичное право",
                        "edu_level": "магистратура",
                        "status": "active",
                        "verification_status": "verified",
                        "points_cached": 120,
                        "level_cached": "graduate",
                        "personal_discount": 0,
                        "referral_code": "SERGEY2026",
                    },
                    connection=connection,
                )
                result["createdRows"] += 1
        return result
