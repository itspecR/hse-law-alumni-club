#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/lib/mariadb_native.py" restore --snapshot "${SNAPSHOT_FILE:?Укажите зашифрованный снимок}" --target-env "${RESTORE_ENV_FILE:?Укажите Compose env отдельного пустого контура}"
