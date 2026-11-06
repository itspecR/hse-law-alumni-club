#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/lib/mariadb_native.py" preflight
