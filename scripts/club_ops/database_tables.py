import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from django.apps import apps


def model_tables():
    return {model._meta.db_table: model for model in apps.get_app_config("club_data").get_models()}


def normalized(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID | Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    return value


def fingerprint(rows):
    records = sorted(
        json.dumps(normalized(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows
    )
    digest = hashlib.sha256()
    for record in records:
        encoded = record.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return {"rows": len(rows), "sha256": digest.hexdigest()}


def table_rows(model):
    fields = [field for field in model._meta.local_fields if field.column]
    return [
        {field.column: row[field.attname] for field in fields}
        for row in model.objects.values(*(field.attname for field in fields))
    ]
