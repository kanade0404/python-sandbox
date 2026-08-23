#!/usr/bin/env python3
"""Generate the corpus-ext fixtures.

The cases share one schema, so they are written from a single table here rather
than by hand -- the same reason M3's `own/*` cases all carry an identical
`schema.sql`. Re-running this script is idempotent.

Fixture format is M3's, verbatim: `corpus/FORMAT.md` in the M3 tree. The point
of corpus-ext is the *expression* half of nullability, which Phase 1 cannot see
at all because EXPLAIN attributes no base-table column to a computed output.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "cases" / "phase2"

SCHEMA = """\
-- The shared shape for corpus-ext: one table with a NOT NULL numeric, a
-- nullable numeric, and a NOT NULL grouping key; plus a child table whose only
-- payload column is nullable. Every NULL in a result is therefore attributable
-- either to `bonus`/`note` or to the query shape, never to an accident.
CREATE TABLE m (
    id     integer PRIMARY KEY,
    label  text    NOT NULL,
    amount numeric NOT NULL,
    bonus  numeric,
    grp    integer NOT NULL
);

CREATE TABLE n (
    id   integer PRIMARY KEY,
    m_id integer NOT NULL REFERENCES m(id),
    note text
);
"""

SEED = """\
-- m=1 has a child with a note; m=2 has a child with a NULL note; m=3 has no
-- child at all. grp 10 holds rows with a bonus, grp 20 holds only NULL bonuses.
INSERT INTO m (id, label, amount, bonus, grp) VALUES
    (1, 'first',  5.00, 1.50, 10),
    (2, '',       0.00, NULL, 10),
    (3, 'third',  7.00, NULL, 20);
INSERT INTO n (id, m_id, note) VALUES
    (100, 1, 'a note'),
    (101, 2, NULL);
"""

CASES: list[dict] = [
    {
        "name": "aggregate_sum_no_groupby",
        "query": "SELECT sum(m.amount) AS total, count(*) AS n, max(m.id) AS hi FROM m",
        "columns": [("total", True), ("n", False), ("hi", True)],
        "notes": (
            "An ungrouped aggregate emits exactly one row even over an empty table. "
            "SUM and MAX of zero rows are NULL; COUNT of zero rows is 0. The whole "
            "row is decided by the function, not by any column's catalog entry, "
            "which is precisely what an EXPLAIN-driven engine cannot see."
        ),
    },
    {
        "name": "aggregate_sum_groupby_notnull_arg",
        "query": "SELECT m.grp, sum(m.amount) AS total, count(*) AS n FROM m GROUP BY m.grp",
        "columns": [("grp", False), ("total", False), ("n", False)],
        "notes": (
            "With a plain GROUP BY, every emitted group holds at least one row, and "
            "`m.amount` is NOT NULL -- so SUM over the group cannot be NULL. This is "
            "the case that separates 'aggregates are nullable' from 'aggregates are "
            "nullable when their input is or their input may be empty'."
        ),
    },
    {
        "name": "aggregate_sum_groupby_nullable_arg",
        "query": "SELECT m.grp, sum(m.bonus) AS total FROM m GROUP BY m.grp",
        "columns": [("grp", False), ("total", True)],
        "notes": (
            "The mirror of the previous case: same shape, same GROUP BY, but the "
            "aggregated column is nullable, so a group in which every row's `bonus` "
            "is NULL yields SUM = NULL. Seeded so that grp=20 is exactly such a "
            "group -- the NULL is observed, not hypothetical."
        ),
    },
    {
        "name": "coalesce_chain",
        "query": (
            "SELECT coalesce(n.note, m.label) AS a,\n"
            "       coalesce(n.note, m.label, 'fallback') AS b,\n"
            "       coalesce(n.note, n.note) AS c\n"
            "FROM m LEFT JOIN n ON n.m_id = m.id"
        ),
        "columns": [("a", False), ("b", False), ("c", True)],
        "notes": (
            "COALESCE is NULL only when EVERY argument is. `a` ends in a NOT NULL "
            "column of the join's preserved side, `b` ends in a literal, and `c` has "
            "nothing but nullable arguments. Getting `c` right rules out the "
            "shortcut 'COALESCE is never NULL'; getting `a` right rules out 'COALESCE "
            "has a nullable argument, therefore nullable'."
        ),
    },
    {
        "name": "case_no_else",
        "query": "SELECT m.id, CASE WHEN m.amount > 0 THEN m.label END AS v FROM m",
        "columns": [("id", False), ("v", True)],
        "notes": (
            "A CASE with no ELSE yields NULL for every row that matches no WHEN, "
            "however non-null the branch results are. Seeded with a zero-amount row "
            "so the NULL really appears."
        ),
    },
    {
        "name": "case_with_else",
        "query": (
            "SELECT CASE WHEN m.amount > 0 THEN m.label ELSE 'zero' END AS v,\n"
            "       CASE WHEN m.amount > 0 THEN n.note ELSE 'zero' END AS w\n"
            "FROM m LEFT JOIN n ON n.m_id = m.id"
        ),
        "columns": [("v", False), ("w", True)],
        "notes": (
            "With an ELSE the CASE is exhaustive, so `v` is NOT NULL. `w` has the "
            "same shape but one nullable branch result, which is enough on its own. "
            "The pair pins down both halves of the rule: nullable if ANY branch is, "
            "or if the ELSE is missing."
        ),
    },
    {
        "name": "nullif_case",
        "query": "SELECT m.label AS raw, nullif(m.label, '') AS v FROM m",
        "columns": [("raw", False), ("v", True)],
        "notes": (
            "NULLIF is the one construct that manufactures a NULL out of two NOT "
            "NULL operands: `nullif(x, y)` is NULL exactly when x = y. `raw` and `v` "
            "are the same catalog column, so nothing but the expression can tell "
            "them apart -- an engine keyed on attribution must report both the same "
            "way and will be wrong about one of them. Seeded with a row whose label "
            "is '' so the NULL is observed."
        ),
    },
    {
        "name": "binary_op_mixed",
        "query": (
            "SELECT m.amount + 1 AS a,\n"
            "       m.amount + m.bonus AS b,\n"
            "       m.amount > 0 AS c,\n"
            "       m.bonus > 0 AS d\n"
            "FROM m"
        ),
        "columns": [("a", False), ("b", True), ("c", False), ("d", True)],
        "notes": (
            "PostgreSQL's operators are strict: the result is NULL iff an operand "
            "is, and that holds for comparisons too -- `NULL > 0` is NULL, not "
            "false. `c` and `d` are the interesting pair, because an engine that "
            "types a comparison as 'boolean, therefore non-null' (sqlc's "
            "typePredicateList does exactly this for IN/BETWEEN) is UNSOUND on `d`."
        ),
    },
    {
        "name": "union_nullability",
        "query": (
            "SELECT m.label AS a, m.label AS b FROM m\n"
            "UNION ALL\n"
            "SELECT n.note, 'literal' FROM n"
        ),
        "columns": [("a", True), ("b", False)],
        "notes": (
            "A set operation merges positionally and a column is nullable exactly "
            "when ANY branch's is. `a` pairs a NOT NULL column with a nullable one "
            "and must come out nullable; `b` pairs two non-null expressions and must "
            "NOT. An engine that inspects only the leading branch -- sqlc returns "
            "`c.outputColumns(qc, n.Larg)` and never looks at Rarg -- calls `a` NOT "
            "NULL, which is unsound, and is right about `b` by accident."
        ),
    },
    {
        "name": "scalar_subquery",
        "query": (
            "SELECT m.id,\n"
            "       (SELECT n.note FROM n WHERE n.m_id = m.id ORDER BY n.id LIMIT 1) AS note,\n"
            "       EXISTS (SELECT 1 FROM n WHERE n.m_id = m.id) AS has_n\n"
            "FROM m"
        ),
        "columns": [("id", False), ("note", True), ("has_n", False)],
        "notes": (
            "A scalar subquery that matches no row evaluates to NULL, so `note` is "
            "nullable however NOT NULL its source column might be -- and here the "
            "source is nullable too. EXISTS is the opposite: it is a total "
            "predicate, true or false, never NULL. Seeded with an m that has no n, "
            "so the correlated subquery really does come back empty."
        ),
    },
    {
        "name": "cast_preserves",
        "query": (
            "SELECT m.id::text AS a,\n"
            "       n.note::text AS b,\n"
            "       (1 + 1)::text AS c\n"
            "FROM m LEFT JOIN n ON n.m_id = m.id"
        ),
        "columns": [("a", False), ("b", True), ("c", False)],
        "notes": (
            "A cast is value-preserving for NULL: `NULL::text` is NULL. So a cast "
            "must pass its operand's nullability straight through -- `a` stays NOT "
            "NULL, `b` stays nullable, `c` folds two literals. sqlc's "
            "typeTypeCast (internal/core/analyzer/expr.go:728-742) returns the "
            "target type with `nullable` left at its zero value, i.e. false, so it "
            "would report `b` as NOT NULL. That is the unsound direction."
        ),
    },
]

PROVENANCE = (
    "hand-written for alt-SQLAlchemy M4 (corpus-ext): the expression-level "
    "nullability Phase 1 structurally cannot see"
)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        d = ROOT / case["name"]
        d.mkdir(exist_ok=True)
        (d / "schema.sql").write_text(SCHEMA)
        (d / "query.sql").write_text(case["query"].rstrip() + "\n")
        (d / "seed.sql").write_text(SEED)
        (d / "expected.json").write_text(
            json.dumps(
                {
                    "columns": [
                        {"name": name, "nullable": nullable}
                        for name, nullable in case["columns"]
                    ],
                    "provenance": PROVENANCE,
                    "notes": case["notes"],
                },
                indent=2,
            )
            + "\n"
        )
    print(f"wrote {len(CASES)} cases to {ROOT}")


if __name__ == "__main__":
    main()
