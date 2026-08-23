#!/bin/sh
# What the catalog loader models, measured rather than asserted.
set -eu
M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BIN="$M4_DIR/target/release/altsa-analyze"
PROBE="$M4_DIR/harness/ddl_probe.sql"
EC="$M4_DIR/../sqlacodegen-trial/ddl/postgres.sql"

echo "== EC schema: sqlacodegen-trial/ddl/postgres.sql =="
"$BIN" --schema "$EC" --catalog-only
echo

echo "== probe schema: harness/ddl_probe.sql =="
echo "-- catalog + warnings (the warnings ARE the unsupported list)"
"$BIN" --schema "$PROBE" --catalog-only
echo
echo "-- SELECT * FROM base   (serial / bigserial / identity / enum / generated / check)"
"$BIN" --schema "$PROBE" --query-sql "SELECT * FROM base" 2>&1
echo
echo "-- SELECT * FROM child  (ALTER ADD COLUMN, SET NOT NULL, DROP COLUMN)"
"$BIN" --schema "$PROBE" --query-sql "SELECT * FROM child" 2>&1
echo
echo "-- SELECT * FROM composite_pk  (table-level PRIMARY KEY implies NOT NULL)"
"$BIN" --schema "$PROBE" --query-sql "SELECT * FROM composite_pk" 2>&1
