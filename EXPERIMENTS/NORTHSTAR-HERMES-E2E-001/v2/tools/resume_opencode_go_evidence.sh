#!/usr/bin/env bash
set -euo pipefail

v2="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime="${NORTHSTAR_GO_RUNTIME:-/home/psm/northstar/state/NORTHSTAR_OPENCODE_GO_E2E_004}"
objective="${NORTHSTAR_GO_OBJECTIVE:-NORTHSTAR-GO-E2E-004}"

cd "$v2"
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests

set +e
PYTHONDONTWRITEBYTECODE=1 python3 ntv/ntv_opencode_go_acceptance.py --runtime "$runtime" --objective-id "$objective" --output "${TMPDIR:-/tmp}/northstar_opencode_go_acceptance.recheck.json"
verifier_rc=$?
set -e
if [[ "$verifier_rc" -ne 1 ]]; then
  echo "Expected bounded NORTHSTAR_FAIL verifier exit 1; observed $verifier_rc" >&2
  exit 3
fi

python3 src/persistent_control_plane_debian.py status --runtime "$runtime"
echo "CHECKPOINT_RECHECK_PASS / EXPECTED_PRODUCT_VERDICT=NORTHSTAR_FAIL"
