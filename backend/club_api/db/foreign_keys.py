import hashlib

from django.db import models


def apply_actions(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != "mysql":
        return
    quote = schema_editor.quote_name
    for model in apps.get_app_config("club_data").get_models():
        table = model._meta.db_table
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, table)
        for field in model._meta.local_fields:
            if not isinstance(field, models.ForeignKey):
                continue
            action = {models.CASCADE: "CASCADE", models.SET_NULL: "SET NULL"}.get(field.remote_field.on_delete)
            if not action:
                continue
            old_name = next(
                (
                    name
                    for name, value in constraints.items()
                    if value.get("foreign_key") and value["columns"] == [field.column]
                ),
                None,
            )
            name = "club_fk_" + hashlib.sha256((table + ":" + field.column).encode()).hexdigest()[:24]
            if old_name == name:
                continue
            related = field.related_model._meta.db_table
            target = field.target_field.column
            if old_name:
                schema_editor.execute(f"ALTER TABLE {quote(table)} DROP FOREIGN KEY {quote(old_name)}")
            schema_editor.execute(
                f"ALTER TABLE {quote(table)} ADD CONSTRAINT {quote(name)} FOREIGN KEY ({quote(field.column)}) REFERENCES {quote(related)} ({quote(target)}) ON DELETE {action}"
            )
