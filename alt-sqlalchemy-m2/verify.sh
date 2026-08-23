#!/bin/sh
# Re-run every M2 gate. Assumes the postgres container is up:
#   docker run -d --name altsa-m2-pg -e POSTGRES_PASSWORD=postgres \
#     -e POSTGRES_USER=postgres -e POSTGRES_DB=ec -p 55432:5432 postgres:16
#   docker exec -i altsa-m2-pg psql -U postgres -d ec < ../sqlacodegen-trial/ddl/postgres.sql
set -e
cd "$(dirname "$0")"

PG="postgresql+psycopg://postgres:postgres@localhost:55432/ec"
SQLITE="sqlite:///$(pwd)/evidence/ec_sqlite.db"

echo "== G1 generate (postgres) =="
uv run python -m altsa_gen --url "$PG" --config joins.toml \
  --out generated/pg --report evidence/gen_pg_report.txt

echo "== G1 generate (sqlite) =="
uv run python -m altsa_gen --url "$SQLITE" --config joins.sqlite.toml \
  --out generated/sqlite --report evidence/gen_sqlite_report.txt

echo "== G7 determinism =="
rm -rf /tmp/altsa_det1 /tmp/altsa_det2
uv run python -m altsa_gen --url "$PG" --config joins.toml --out /tmp/altsa_det1 2>/dev/null
uv run python -m altsa_gen --url "$PG" --config joins.toml --out /tmp/altsa_det2 2>/dev/null
diff -r /tmp/altsa_det1 /tmp/altsa_det2 && echo "  byte-identical"
diff /tmp/altsa_det1/facade.py generated/pg/facade.py && echo "  committed output matches"

echo "== runtime + generator + generated: pyright strict =="
uv run pyright --project proofs/pyrightconfig.core.json | tee evidence/pyright_core.txt | tail -1

echo "== G2 positive =="
uv run pyright --project proofs/pyrightconfig.positive.json \
  | tee evidence/pyright_positive.txt | tail -1
uv run python -m proofs.run_checks 2>&1 | tee evidence/leakage_check.txt | tail -1
uv run python proofs/compare_m1_reveals.py \
  | tee evidence/g2_m1_equivalence.txt | tail -1

echo "== G3 + G5 negative =="
uv run pyright --project proofs/pyrightconfig.negative.json --outputjson \
  > evidence/pyright_negative.json || true
uv run python proofs/check_negative.py < evidence/pyright_negative.json \
  | tee evidence/check_negative.txt | tail -1

echo "== G4 patterns =="
uv run pyright --project proofs/pyrightconfig.patterns.json \
  | tee evidence/pyright_patterns.txt | tail -1
uv run python -m patterns.run_patterns > evidence/run_patterns_pg.log 2>&1
tail -1 evidence/run_patterns_pg.log > /dev/null && echo "  patterns ran"
uv run python -m patterns.smoke_sqlite > evidence/run_sqlite_smoke.log 2>&1
echo "  sqlite smoke ran"

echo "== G6 fidelity =="
uv run python -m patterns.fidelity_check > evidence/fidelity_pg.txt 2>&1
tail -1 evidence/fidelity_pg.txt
