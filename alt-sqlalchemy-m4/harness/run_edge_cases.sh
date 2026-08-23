#!/bin/sh
# Behaviour on the edges: errors, ambiguity, unknown functions, exotic shapes.
# Everything here must either answer soundly or fail with a clear message --
# never crash, never silently claim NOT NULL.
set -eu
M4_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BIN="$M4_DIR/target/release/altsa-analyze"
S="$M4_DIR/corpus-ext/cases/phase2/coalesce_chain/schema.sql"

probe() {
    echo "--------------------------------------------------------------"
    echo "SQL: $1"
    "$BIN" --schema "$S" --query-sql "$1" || echo "(exit $?)"
    echo
}

probe "SELECT nope FROM m"
probe "SELECT id FROM nosuchtable"
probe "SELECT id FROM m JOIN n ON n.m_id = m.id"
probe "SELECT weird_udf(m.label) AS v FROM m"
probe "SELECT m.label FROM m WHERE m.id = \${target}"
probe "SELECT m.label FROM m WHERE m.id = \${target} OR \${target} IS NULL"
probe "SELECT count(*) FILTER (WHERE m.amount > 0) AS c, sum(m.amount) FILTER (WHERE m.amount > 0) AS s FROM m GROUP BY m.grp"
probe "SELECT sum(m.amount) OVER (PARTITION BY m.grp) AS running, row_number() OVER () AS rn, count(*) OVER () AS c FROM m"
probe "SELECT m.grp, sum(m.amount) AS total FROM m GROUP BY ROLLUP (m.grp)"
probe "WITH RECURSIVE t(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM t WHERE n < 5) SELECT n FROM t"
probe "SELECT g FROM generate_series(1, 10) AS g"
probe "SELECT m.label, x.v FROM m CROSS JOIN LATERAL (SELECT n.note AS v FROM n WHERE n.m_id = m.id) x"
probe "SELECT greatest(m.bonus, m.amount) AS g, least(m.bonus, m.bonus) AS l FROM m"
probe "SELECT m.id IS NULL AS a, m.bonus IS NOT NULL AS b, (m.amount > 0) IS TRUE AS c FROM m"
probe "SELECT m.bonus IS DISTINCT FROM m.amount AS a, m.bonus IN (1, 2) AS b, m.bonus AND true AS c FROM m"
probe "SELECT * FROM m LEFT JOIN n ON n.m_id = m.id"
probe "INSERT INTO n (id, m_id, note) VALUES (1, 1, 'x') RETURNING id, note"
probe "UPDATE m SET label = 'x' WHERE id = 1 RETURNING id, bonus"
probe "SELECT m.label AS \"forced!\", m.id AS \"soft?\" FROM m"
probe "SELECT 1 AS a; SELECT 2 AS b"
probe "SELECT FROM m"
probe "this is not sql"
