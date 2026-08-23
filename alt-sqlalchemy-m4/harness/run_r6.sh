#!/bin/sh
# Gate R6: determinism + per-case wall time.
set -eu
M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export PYTHONPATH="$M4_DIR/harness"
uv run --project "$M4_DIR/harness" python "$M4_DIR/harness/determinism_and_perf.py" "$@" \
    | tee "$M4_DIR/evidence/determinism_and_perf.txt"
