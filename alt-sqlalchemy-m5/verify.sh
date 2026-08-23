#!/usr/bin/env bash
# Re-runs every M5 gate and tees each artifact into evidence/.
#
#   ./verify.sh [dsn]
#
# Needs the container from the README:
#   docker run -d --name altsa-m5-pg -e POSTGRES_PASSWORD=postgres \
#     -e POSTGRES_USER=postgres -e POSTGRES_DB=altsa -p 55438:5432 postgres:16
#   docker exec -i altsa-m5-pg psql -U postgres -d altsa \
#     < ../sqlacodegen-trial/ddl/postgres.sql
set -euo pipefail

cd "$(dirname "$0")"
DSN="${1:-postgresql://postgres:postgres@localhost:55438/altsa}"
mkdir -p evidence

# `-B` and m5link's `sys.dont_write_bytecode` keep M2's and M3's trees clean.
PY=(uv run python -B)

echo "== E1a  regenerate both layers (fresh, against the live DB)"
"${PY[@]}" regen.py --url "$DSN" --repeat 3 \
    --timings evidence/gen_timings.json 2>&1 | tee evidence/generation.txt

echo "== E1b  generation is deterministic"
{
  cp -R generated_a evidence/.gen_a_prev
  cp -R generated_b evidence/.gen_b_prev
  "${PY[@]}" regen.py --url "$DSN" --repeat 1 >/dev/null
  if diff -r evidence/.gen_a_prev generated_a >/dev/null 2>&1 \
     && diff -r evidence/.gen_b_prev generated_b >/dev/null 2>&1; then
    echo "DETERMINISM: PASS -- both layers byte-identical across runs"
  else
    echo "DETERMINISM: FAIL"
    diff -r evidence/.gen_a_prev generated_a || true
    diff -r evidence/.gen_b_prev generated_b || true
  fi
  rm -rf evidence/.gen_a_prev evidence/.gen_b_prev
} 2>&1 | tee evidence/determinism.txt

echo "== E1c  pyright --strict over app/ + both generated packages + bench/"
uv run pyright --outputjson > evidence/pyright.json || true
uv run pyright 2>&1 | tee evidence/pyright.txt

echo "== E1d  no Any / no leaked types in app-level signatures"
"${PY[@]}" proofs/no_any.py 2>&1 | tee evidence/no_any.txt

echo "== E1e  the scenario, against live PostgreSQL"
"${PY[@]}" -m app.scenario --url "$DSN" 2>&1 | tee evidence/scenario.log

echo "== E2   benchmarks"
"${PY[@]}" -m bench.hotpath --url "$DSN" --iterations 2000 --warmup 200 \
    --json evidence/bench.json 2>&1 | tee evidence/bench.txt

echo "== LOC"
{
  echo "hand-written in M5"
  wc -l app/*.py bench/*.py proofs/*.py m5link.py regen.py joins.toml queries/*.sql
  echo
  echo "generated into M5"
  wc -l generated_a/*.py generated_b/*.py
  echo
  echo "imported, not copied (M2 + M3, unchanged)"
  wc -l ../alt-sqlalchemy-m2/altsa_runtime/*.py ../alt-sqlalchemy-m2/altsa_gen/*.py \
        ../alt-sqlalchemy-m3/altsa_sqlgen/*.py
} 2>&1 | tee evidence/loc.txt

echo
echo "all gates run; artifacts in evidence/"
