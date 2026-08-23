"""Live-database oracle: check the engine's NOT NULL claims against real rows.

    uv run python -m corpus.oracle --url <dsn> [--engine <name>]

`runner.py` compares an engine to a hand-written `expected.json`, so a wrong
expectation would hide a real bug. This closes that loop with the database
itself.

The property checked is deliberately ASYMMETRIC:

    a column the engine called NOT NULL must never contain NULL in an actual
    result row

Observing a NULL under a not-null claim is UNSOUND no matter what
`expected.json` says -- it is a counterexample, and a counterexample outranks
an expectation. The converse is NOT checked: a column the engine called
nullable is perfectly entitled to contain no NULLs in the seeded data. Absence
of NULLs is not a proof of non-nullability, and demanding one would only
encourage under-approximation, which is the failure mode this whole design is
built to avoid.

Only cases with a `seed.sql` are checked; a case with no seed produces no rows
and therefore no evidence either way.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from .cases import CASES_ROOT, Case, apply_schema, connect, load_cases
from .engines import ENGINES, Engine


@dataclass(frozen=True, slots=True)
class Observation:
    case: Case
    rows: int
    claimed_not_null: tuple[str, ...]
    violations: tuple[tuple[str, int], ...]
    """(column name, row index) pairs where a NOT NULL claim saw a NULL."""
    observed_nulls: tuple[str, ...]
    """Nullable-claimed columns that actually did produce a NULL -- evidence
    that the seed exercises the interesting path."""
    error: str | None = None


def observe(url: str, engine: Engine, cases: list[Case]) -> list[Observation]:
    seeded = [c for c in cases if c.seed is not None]
    out: list[Observation] = []
    with connect(url) as conn:
        for case in seeded:
            try:
                apply_schema(conn, case)
                inferred = engine.analyze(conn, case)
                assert case.seed is not None
                conn.execute(case.seed.encode())
                block = case.block()
                # Every parameter is bound to NULL: the seeded data is what the
                # case is about, and a NULL parameter never invents a NULL in a
                # column that could not otherwise be NULL.
                with conn.cursor() as cur:
                    cur.execute(
                        block.render_psycopg().encode(),
                        {p.name: None for p in block.params} if block.params else None,
                    )
                    rows = cur.fetchall()
            except Exception as exc:  # noqa: BLE001 -- report, never crash the run
                if isinstance(exc, KeyboardInterrupt):
                    raise
                conn.rollback()
                out.append(
                    Observation(
                        case=case,
                        rows=0,
                        claimed_not_null=(),
                        violations=(),
                        observed_nulls=(),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue

            violations: list[tuple[str, int]] = []
            saw_null: set[str] = set()
            for r, row in enumerate(rows):
                for i, col in enumerate(inferred):
                    if i >= len(row):
                        break
                    if row[i] is None:
                        if col.nullable:
                            saw_null.add(col.name)
                        else:
                            violations.append((col.name, r))
            out.append(
                Observation(
                    case=case,
                    rows=len(rows),
                    claimed_not_null=tuple(c.name for c in inferred if not c.nullable),
                    violations=tuple(violations),
                    observed_nulls=tuple(sorted(saw_null)),
                )
            )
    return out


def report(observations: list[Observation], engine_name: str) -> str:
    lines = [f"CORPUS ORACLE -- engine: {engine_name}", "=" * 72, ""]
    total_rows = 0
    total_claims = 0
    violations = 0
    errors = 0

    for o in observations:
        if o.error is not None:
            errors += 1
            lines.append(f"[ERROR   ] {o.case.id}")
            lines.append(f"           {o.error}")
            lines.append("")
            continue
        total_rows += o.rows
        total_claims += len(o.claimed_not_null) * o.rows
        violations += len(o.violations)
        status = "UNSOUND " if o.violations else "OK      "
        lines.append(f"[{status}] {o.case.id}  ({o.rows} row(s))")
        lines.append(
            f"           claimed NOT NULL: "
            f"{', '.join(o.claimed_not_null) if o.claimed_not_null else '(none)'}"
        )
        if o.observed_nulls:
            lines.append(
                f"           NULLs actually observed (all in nullable-claimed "
                f"columns): {', '.join(o.observed_nulls)}"
            )
        for name, r in o.violations:
            lines.append(f"           VIOLATION column {name!r} was NULL in row {r}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("SUMMARY")
    lines.append("")
    lines.append(f"  seeded cases        : {len(observations)}")
    lines.append(f"  result rows         : {total_rows}")
    lines.append(f"  NOT NULL assertions : {total_claims} (column x row)")
    lines.append(f"  violations          : {violations}")
    lines.append(f"  errors              : {errors}")
    lines.append("")
    lines.append(
        f"{'FAIL' if violations or errors else 'PASS'}: "
        f"{violations} unsound observation(s), {errors} error(s)"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corpus.oracle", description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--engine", default="altsa_sqlgen-phase1", choices=sorted(ENGINES))
    ap.add_argument("--cases", type=Path, default=CASES_ROOT)
    ap.add_argument("--only", default=None)
    args = ap.parse_args(argv)

    cases = load_cases(args.cases, only=args.only)
    engine = ENGINES[args.engine]
    observations = observe(args.url, engine, cases)
    print(report(observations, engine.name))
    bad = sum(len(o.violations) for o in observations) + sum(
        1 for o in observations if o.error is not None
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
