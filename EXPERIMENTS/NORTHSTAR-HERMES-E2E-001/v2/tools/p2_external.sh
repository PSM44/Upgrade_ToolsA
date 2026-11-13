#!/usr/bin/env bash
set -euo pipefail
v2="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 "$v2/tools/p2_external.py" "$@"
