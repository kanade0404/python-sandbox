#!/bin/sh
# Re-run every M3 gate, writing each artifact under evidence/.
#
# Assumes a postgres:16 container is up with the EC schema applied:
#
#   docker run -d --name altsa-m3-pg -e POSTGRES_PASSWORD=postgres \
#     -e POSTGRES_DB=altsa -p 55434:5432 postgres:16
#   docker exec -i altsa-m3-pg psql -U postgres -d altsa \
#     < ../sqlacodegen-trial/ddl/postgres.sql
#   docker exec altsa-m3-pg psql -U postgres -c "CREATE DATABASE corpus;"
#
# The corpus runs against a SEPARATE database, because every case drops and
# recreates the `public` schema.
set -e
cd "$(dirname "$0")"

EC_URL="${EC_URL:-postgresql://postgres:postgres@127.0.0.1:55434/altsa}"
CORPUS_URL="${CORPUS_URL:-postgresql://postgres:postgres@127.0.0.1:55434/corpus}"
mkdir -p evidence

echo "== H1 annotation parsing =="
uv run pytest -q 2>&1 | tee evidence/h1_pytest.txt

echo
echo "== H2 generation =="
uv run python -m altsa_sqlgen --url "$EC_URL" --queries queries --out generated --report \
  2>&1 | tee evidence/h2_generation.txt

echo
echo "== H2 pyright: generator + corpus + tests (core lane) =="
uv run pyright --project proofs/pyrightconfig.core.json 2>&1 | tee evidence/h2_pyright_core.txt

echo
echo "== H2 pyright: generated code + positive proof =="
uv run pyright --project proofs/pyrightconfig.positive.json 2>&1 \
  | tee evidence/h2_pyright_positive.txt

echo
echo "== H2 no Any / no psycopg leakage =="
uv run python proofs/run_checks.py 2>&1 | tee evidence/h2_leakage.txt

echo
echo "== H2 negative proofs (two-way) =="
uv run pyright --project proofs/pyrightconfig.negative.json --outputjson \
  > evidence/h2_pyright_negative.json || true
uv run python proofs/check_negative.py < evidence/h2_pyright_negative.json \
  2>&1 | tee evidence/h2_check_negative.txt

echo
echo "== H3 corpus soundness (phase 1) =="
uv run python -m corpus.runner --url "$CORPUS_URL" --json evidence/h3_corpus.json \
  2>&1 | tee evidence/h3_corpus_runner.txt

echo
echo "== H3 control: sqlx's plan walker, verbatim =="
uv run python -m corpus.runner --url "$CORPUS_URL" --engine sqlx-verbatim-control \
  2>&1 | tee evidence/h3_control_sqlx_verbatim.txt || true

echo
echo "== H3 control: always-nullable =="
uv run python -m corpus.runner --url "$CORPUS_URL" --engine always-nullable-control \
  2>&1 | tee evidence/h3_control_always_nullable.txt

echo
echo "== H4 live-database oracle =="
uv run python -m corpus.oracle --url "$CORPUS_URL" 2>&1 | tee evidence/h4_oracle.txt

echo
echo "== H4 control: the oracle catches the sqlx rule =="
uv run python -m corpus.oracle --url "$CORPUS_URL" --engine sqlx-verbatim-control \
  2>&1 | tee evidence/h4_oracle_control.txt || true

echo
echo "== H5 unknown-column override is a clean error =="
{
  echo '$ uv run python -m altsa_sqlgen --queries proofs/bad_queries --out /tmp/altsa-m3-bad'
  uv run python -m altsa_sqlgen --url "$EC_URL" --queries proofs/bad_queries \
    --out /tmp/altsa-m3-bad 2>&1 || echo "EXIT=$?"
} 2>&1 | tee evidence/h5_bad_override.txt

echo
echo "== H6 determinism =="
rm -rf /tmp/altsa-m3-det1 /tmp/altsa-m3-det2
uv run python -m altsa_sqlgen --url "$EC_URL" --queries queries --out /tmp/altsa-m3-det1 \
  > /dev/null
uv run python -m altsa_sqlgen --url "$EC_URL" --queries queries --out /tmp/altsa-m3-det2 \
  > /dev/null
{
  echo "DETERMINISM"
  echo "==========="
  echo
  echo "Two generator runs against the SAME live PostgreSQL 16, into two"
  echo "different output directories, then a recursive diff:"
  echo
  echo '  $ uv run python -m altsa_sqlgen --url ... --out /tmp/altsa-m3-det1'
  echo '  $ uv run python -m altsa_sqlgen --url ... --out /tmp/altsa-m3-det2'
  echo '  $ diff -r /tmp/altsa-m3-det1 /tmp/altsa-m3-det2'
  diff -r /tmp/altsa-m3-det1 /tmp/altsa-m3-det2 && echo "  DIFF_EXIT=0          <- empty diff"
  echo
  echo "... and against the checked-in generated/ tree:"
  echo '  $ diff -r -x __pycache__ generated /tmp/altsa-m3-det1'
  diff -r -x __pycache__ generated /tmp/altsa-m3-det1 \
    && echo "  DIFF_EXIT=0          <- empty diff"
  echo
  echo "What makes it deterministic"
  echo "  * .sql files are visited in sorted order, queries in file order"
  echo "  * imports are rendered from a SORTED set"
  echo "  * nothing read from the clock, the DSN, the hostname or the env"
  echo "  * the only inputs are the .sql text and the server's own answers"
} 2>&1 | tee evidence/h6_determinism.txt

echo
echo "== H7 end-to-end against live PostgreSQL =="
uv run python proofs/e2e.py --url "$EC_URL" 2>&1 | tee evidence/h7_e2e.txt

echo
echo "== all gates run; see evidence/ =="
