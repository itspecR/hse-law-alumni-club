import re

from django.db import connection

from club_ops.database_tables import model_tables


def runtime_grants(database, user, host):
    if (
        not re.fullmatch(r"[a-zA-Z0-9_]{1,64}", database)
        or not re.fullmatch(r"[a-zA-Z0-9_]{1,32}", user)
        or not re.fullmatch(r"[a-zA-Z0-9_.:%-]{1,255}", host)
    ):
        raise ValueError("Некорректное имя базы или аккаунта MariaDB")
    account = f"'{user}'@'{host}'"
    commands = [f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {account}"]
    for name in sorted(model_tables()):
        if name == "club_settings":
            continue
        if name == "directus_users":
            privileges = "SELECT (`id`,`email`,`password`,`first_name`,`last_name`,`role`,`status`,`provider`,`tfa_secret`), INSERT (`id`,`email`,`password`,`first_name`,`last_name`,`role`,`status`,`provider`), UPDATE (`email`,`password`,`first_name`,`last_name`,`status`), DELETE"
        elif name == "directus_roles":
            privileges = "SELECT (`id`,`name`)"
        else:
            privileges = "SELECT, INSERT, UPDATE, DELETE"
        commands.append(f"GRANT {privileges} ON `{database}`.`{name}` TO {account}")
    return commands


def configure_runtime_role(values):
    if connection.vendor != "mysql" or not connection.mysql_is_mariadb:
        raise ValueError("Нужна MariaDB")
    user = values.get("CHECKOUT_DB_USER", "club_api")
    host = values.get("CHECKOUT_DB_HOST", "localhost")
    password = values.get("CHECKOUT_DB_PASSWORD", "")
    if len(password) < 32 or len(password) > 128 or "\n" in password or "\r" in password:
        raise ValueError("Нужен отдельный случайный пароль роли API длиной 32–128 символов")
    commands = runtime_grants(connection.settings_dict["NAME"], user, host)
    with connection.cursor() as cursor:
        cursor.execute("SELECT CURRENT_USER()")
        if cursor.fetchone()[0] == f"{user}@{host}":
            raise ValueError("Аккаунт API должен отличаться от аккаунта оператора")
        cursor.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, host, password))
        cursor.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, host, password))
        for query in commands:
            cursor.execute(query)
