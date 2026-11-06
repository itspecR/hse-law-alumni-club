#!/usr/bin/env bash
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${CLUB_TEST_DATABASE_URL:?Нужна отдельная локальная MariaDB django_migration_test}"
uv run --directory "$REPO_DIR/scripts" --frozen python - <<'PY'
import os
from urllib.parse import urlsplit

url = os.environ["CLUB_TEST_DATABASE_URL"]
parsed = urlsplit(url)
if parsed.scheme not in ("mariadb", "mysql") or parsed.path != "/django_migration_test" or parsed.hostname not in ("127.0.0.1", "localhost"):
    raise ValueError("Разрешена только локальная одноразовая MariaDB django_migration_test")
os.environ["CHECKOUT_DATABASE_URL"] = url
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "club_api.django_settings")
import django
django.setup()
from django.core.management import call_command
from club_ops.database_tables import model_tables
from club_ops.mariadb_roles import configure_runtime_role
call_command("migrate", interactive=False)
if any(model.objects.exists() for model in model_tables().values()):
    raise ValueError("Тестовая база должна быть пустой")
configure_runtime_role({"CHECKOUT_DB_USER": "club_api", "CHECKOUT_DB_HOST": "localhost", "CHECKOUT_DB_PASSWORD": "synthetic-restricted-mariadb-test-only"})
PY
uv run --directory "$REPO_DIR/backend" --frozen pytest -q tests/python "$@"
