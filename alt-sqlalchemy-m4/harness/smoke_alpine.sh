#!/bin/sh
# Gate R1: the static musl binary runs on a libc it was never linked against.
set -eu
M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REPO=$(CDPATH= cd -- "$M4_DIR/.." && pwd)
BIN=target-musl/x86_64-unknown-linux-musl/release/altsa-analyze

echo "== host-side facts =="
ls -l "$M4_DIR/$BIN"
file "$M4_DIR/$BIN"
echo

echo "== inside alpine:3.20 (no rust, no glibc, no postgres) =="
docker run --rm --platform linux/amd64 \
    -v "$REPO":/work -w /work/alt-sqlalchemy-m4 \
    alpine:3.20 sh -c '
        echo "alpine: $(cat /etc/alpine-release)"
        echo "ldd says:"; ldd '"$BIN"' 2>&1 || true
        echo
        echo "--- own/left_join_nested_right (the sqlc bug case) ---"
        ./'"$BIN"' \
            --schema ../alt-sqlalchemy-m3/corpus/cases/own/left_join_nested_right/schema.sql \
            --query  ../alt-sqlalchemy-m3/corpus/cases/own/left_join_nested_right/query.sql
        echo
        echo "--- phase2/coalesce_chain ---"
        ./'"$BIN"' \
            --schema corpus-ext/cases/phase2/coalesce_chain/schema.sql \
            --query  corpus-ext/cases/phase2/coalesce_chain/query.sql
        echo
        echo "--- EC schema catalog ---"
        ./'"$BIN"' --schema ../sqlacodegen-trial/ddl/postgres.sql --catalog-only --timing
    '
