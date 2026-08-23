#!/bin/sh
# Regenerate every evidence artefact that does not need a fresh build.
#
#   sh harness/collect_evidence.sh <dsn>
#
# The DSN is only needed by the two Phase 1 runs and the oracle; the Rust
# engine's own scores are produced without it.
set -eu

M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DSN=${1:-}
EV="$M4_DIR/evidence"
mkdir -p "$EV"

RUN="uv run --project $M4_DIR/harness python"
export PYTHONPATH="$M4_DIR/harness"

echo "== [1/6] rust engine over the M3 corpus (26 cases) =="
$RUN "$M4_DIR/harness/score.py" --engine rust \
    --json "$EV/scores_rust_m3corpus.json" \
    --warnings "$EV/warnings_rust_m3corpus.txt" \
    > "$EV/scores_rust_m3corpus.txt" 2>&1 || true
tail -12 "$EV/scores_rust_m3corpus.txt"

echo "== [2/6] rust engine over corpus-ext (11 cases) =="
$RUN "$M4_DIR/harness/score.py" --engine rust --cases corpus-ext/cases \
    --json "$EV/scores_rust_corpusext.json" \
    --warnings "$EV/warnings_rust_corpusext.txt" \
    > "$EV/scores_rust_corpusext.txt" 2>&1 || true
tail -12 "$EV/scores_rust_corpusext.txt"

if [ -z "$DSN" ]; then
    echo "no DSN given -- skipping the Phase 1 runs and the oracle"
    exit 0
fi

echo "== [3/6] phase 1 over the M3 corpus (baseline) =="
$RUN "$M4_DIR/harness/score.py" --engine altsa_sqlgen-phase1 --url "$DSN" \
    --json "$EV/scores_phase1_m3corpus.json" \
    > "$EV/scores_phase1_m3corpus.txt" 2>&1 || true
tail -12 "$EV/scores_phase1_m3corpus.txt"

echo "== [4/6] phase 1 over corpus-ext (the delta) =="
$RUN "$M4_DIR/harness/score.py" --engine altsa_sqlgen-phase1 --url "$DSN" \
    --cases corpus-ext/cases \
    --json "$EV/scores_phase1_corpusext.json" \
    > "$EV/scores_phase1_corpusext.txt" 2>&1 || true
tail -20 "$EV/scores_phase1_corpusext.txt"

echo "== [5/6] oracle: rust engine vs live PostgreSQL, M3 corpus =="
$RUN "$M4_DIR/harness/oracle.py" --url "$DSN" \
    > "$EV/oracle_rust_m3corpus.txt" 2>&1 || true
tail -10 "$EV/oracle_rust_m3corpus.txt"

echo "== [6/6] oracle: rust engine vs live PostgreSQL, corpus-ext =="
$RUN "$M4_DIR/harness/oracle.py" --url "$DSN" --cases corpus-ext/cases \
    > "$EV/oracle_rust_corpusext.txt" 2>&1 || true
tail -10 "$EV/oracle_rust_corpusext.txt"
