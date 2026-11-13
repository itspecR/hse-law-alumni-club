#!/usr/bin/env bash
set -euo pipefail
native="$(dirname "${BASH_SOURCE[0]}")/lib/mariadb_native.py"
snapshot="${SNAPSHOT_FILE:?Укажите зашифрованный снимок}"
if [[ -n "${RESTORE_SITE:-}" ]]; then
  exec python3 "$native" restore-site --snapshot "$snapshot" --site "$RESTORE_SITE" ${SNAPSHOT_ENV_FILE:+--snapshot-env "$SNAPSHOT_ENV_FILE"}
fi
exec python3 "$native" restore --snapshot "$snapshot" --target-env "${RESTORE_ENV_FILE:?Укажите Compose env отдельного пустого контура или RESTORE_SITE с адресом сайта}"
