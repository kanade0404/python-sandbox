#!/bin/sh
# Timed clean native build + test run, for gate R1.
set -eu

M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "== toolchain =="
cargo --version
rustc --version
uname -sm
echo

echo "== cargo clean =="
cargo clean --manifest-path "$M4_DIR/Cargo.toml"
echo

echo "== timed clean release build =="
time cargo build --release --manifest-path "$M4_DIR/Cargo.toml"
echo

echo "== timed incremental (no-op) build =="
time cargo build --release --manifest-path "$M4_DIR/Cargo.toml"
echo

echo "== binary =="
ls -l "$M4_DIR/target/release/altsa-analyze"
file "$M4_DIR/target/release/altsa-analyze"
echo

echo "== unit tests =="
cargo test --release --manifest-path "$M4_DIR/Cargo.toml"
echo

echo "== smoke run =="
"$M4_DIR/target/release/altsa-analyze" \
    --schema "$M4_DIR/../alt-sqlalchemy-m3/corpus/cases/own/left_join_nested_right/schema.sql" \
    --query  "$M4_DIR/../alt-sqlalchemy-m3/corpus/cases/own/left_join_nested_right/query.sql" \
    --timing
echo

echo "== EC schema (sqlacodegen-trial/ddl/postgres.sql) loads clean =="
"$M4_DIR/target/release/altsa-analyze" \
    --schema "$M4_DIR/../sqlacodegen-trial/ddl/postgres.sql" --catalog-only --timing
"$M4_DIR/target/release/altsa-analyze" \
    --schema "$M4_DIR/../sqlacodegen-trial/ddl/postgres.sql" \
    --query-sql "SELECT u.email, u.metadata, o.id AS order_id, coalesce(p.amount, 0) AS paid, count(oi.product_id) AS items
                 FROM users u
                 LEFT JOIN orders o ON o.user_id = u.id
                 LEFT JOIN payments p ON p.order_id = o.id
                 LEFT JOIN order_items oi ON oi.order_id = o.id
                 GROUP BY u.email, u.metadata, o.id, p.amount"
"$M4_DIR/target/release/altsa-analyze" \
    --schema "$M4_DIR/../sqlacodegen-trial/ddl/postgres.sql" \
    --query-sql "SELECT sa.*, a.label FROM accounts a LEFT JOIN staff_accounts sa ON sa.id = a.id"
