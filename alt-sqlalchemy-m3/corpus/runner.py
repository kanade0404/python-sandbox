"""Score an engine against every corpus case.

    uv run python -m corpus.runner --url <dsn> [--engine <name>] [--json out.json]

Exit code is non-zero if any column is UNSOUND. Safe false positives are
reported but never fail the run -- see FORMAT.md, "Soundness direction".
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from altsa_sqlgen.errors import GenerationError

from .cases import CASES_ROOT, Case, apply_schema, connect, load_cases
from .engines import ENGINES, Engine, InferredColumn

Verdict = Literal["PASS", "SAFE-FALSE-POSITIVE", "UNSOUND", "MISMATCH", "ERROR"]

_ORDER: tuple[Verdict, ...] = ("PASS", "SAFE-FALSE-POSITIVE", "UNSOUND", "MISMATCH", "ERROR")


@dataclass(frozen=True, slots=True)
class ColumnResult:
    index: int
    name: str
    expected: str
    got: str
    verdict: Verdict
    how: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: Case
    columns: tuple[ColumnResult, ...]
    error: str | None = None

    @property
    def verdict(self) -> Verdict:
        if self.error is not None:
            return "ERROR"
        for v in ("ERROR", "UNSOUND", "MISMATCH", "SAFE-FALSE-POSITIVE"):
            if any(c.verdict == v for c in self.columns):
                return v
        return "PASS"


def score(case: Case, inferred: list[InferredColumn]) -> tuple[ColumnResult, ...]:
    out: list[ColumnResult] = []
    n = max(len(case.expected), len(inferred))
    for i in range(n):
        exp = case.expected[i] if i < len(case.expected) else None
        got = inferred[i] if i < len(inferred) else None

        if exp is None or got is None:
            out.append(
                ColumnResult(
                    index=i,
                    name=(got.name if got else exp.name if exp else "?"),
                    expected="-" if exp is None else _label(exp.nullable),
                    got="-" if got is None else _label(got.nullable),
                    verdict="MISMATCH",
                    how=got.how if got else "-",
                    detail=(
                        "engine returned more columns than expected"
                        if exp is None
                        else "engine returned fewer columns than expected"
                    ),
                )
            )
            continue

        detail = ""
        if got.name != exp.name:
            detail = f"name mismatch: expected {exp.name!r}, got {got.name!r}"

        if exp.must_be_nullable:
            verdict: Verdict = "PASS" if got.nullable else "UNSOUND"
        else:
            verdict = "PASS" if not got.nullable else "SAFE-FALSE-POSITIVE"
        if detail and verdict == "PASS":
            verdict = "MISMATCH"

        out.append(
            ColumnResult(
                index=i,
                name=exp.name,
                expected=_label(exp.nullable),
                got=_label(got.nullable),
                verdict=verdict,
                how=got.how,
                detail=detail,
            )
        )
    return tuple(out)


def _label(value: bool | str) -> str:
    if value == "unknown":
        return "unknown"
    return "NULL" if value else "NOT NULL"


def run(url: str, engine: Engine, cases: list[Case]) -> list[CaseResult]:
    results: list[CaseResult] = []
    with connect(url) as conn:
        for case in cases:
            try:
                apply_schema(conn, case)
                inferred = engine.analyze(conn, case)
            except (GenerationError, Exception) as exc:  # noqa: B014 -- report, never crash
                if isinstance(exc, KeyboardInterrupt):
                    raise
                conn.rollback()
                results.append(
                    CaseResult(case=case, columns=(), error=f"{type(exc).__name__}: {exc}")
                )
                continue
            results.append(CaseResult(case=case, columns=score(case, inferred)))
    return results


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def report(results: list[CaseResult], engine_name: str) -> str:
    lines: list[str] = []
    lines.append(f"CORPUS RUNNER -- engine: {engine_name}")
    lines.append("=" * 72)
    lines.append("")

    for r in results:
        lines.append(f"[{r.verdict:<19}] {r.case.id}")
        if r.error is not None:
            lines.append(f"      ERROR {r.error}")
            lines.append("")
            continue
        for c in r.columns:
            flag = " " if c.verdict == "PASS" else "*"
            lines.append(
                f"     {flag} {c.index}. {c.name:<22} expected={c.expected:<9} "
                f"got={c.got:<9} via={c.how:<9} {c.verdict}"
            )
            if c.detail:
                lines.append(f"          {c.detail}")
        lines.append("")

    counts: Counter[Verdict] = Counter()
    for r in results:
        for c in r.columns:
            counts[c.verdict] += 1
        if r.error is not None:
            counts["ERROR"] += 1

    by_group: Counter[str] = Counter(r.case.group for r in results)

    lines.append("-" * 72)
    lines.append("SUMMARY")
    lines.append("")
    lines.append(f"  cases        : {len(results)} ({_fmt_counter(by_group)})")
    lines.append(f"  columns      : {sum(len(r.columns) for r in results)}")
    for v in _ORDER:
        lines.append(f"  {v:<20}: {counts[v]}")
    lines.append("")

    fps = [
        (r.case.id, c)
        for r in results
        for c in r.columns
        if c.verdict == "SAFE-FALSE-POSITIVE"
    ]
    if fps:
        lines.append(f"SAFE FALSE POSITIVES ({len(fps)}) -- the Phase1 -> Phase2 precision delta:")
        for case_id, c in fps:
            lines.append(f"  {case_id:<38} {c.name:<20} (via {c.how})")
        lines.append("")

    unsound = [(r.case.id, c) for r in results for c in r.columns if c.verdict == "UNSOUND"]
    if unsound:
        lines.append(f"UNSOUND ({len(unsound)}) -- MUST BE ZERO:")
        for case_id, c in unsound:
            lines.append(f"  {case_id:<38} {c.name:<20} (via {c.how})")
        lines.append("")

    verdict = "FAIL" if (counts["UNSOUND"] or counts["ERROR"] or counts["MISMATCH"]) else "PASS"
    lines.append(
        f"{verdict}: {counts['UNSOUND']} unsound, {counts['MISMATCH']} mismatch, "
        f"{counts['ERROR']} error, {counts['SAFE-FALSE-POSITIVE']} safe-false-positive"
    )
    return "\n".join(lines)


def _fmt_counter(c: Counter[str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(c.items()))


def to_json(results: list[CaseResult], engine_name: str) -> str:
    payload = {
        "engine": engine_name,
        "cases": [
            {
                "id": r.case.id,
                "verdict": r.verdict,
                "provenance": r.case.provenance,
                "notes": r.case.notes,
                "error": r.error,
                "columns": [
                    {
                        "index": c.index,
                        "name": c.name,
                        "expected": c.expected,
                        "got": c.got,
                        "verdict": c.verdict,
                        "how": c.how,
                        "detail": c.detail,
                    }
                    for c in r.columns
                ],
            }
            for r in results
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="corpus.runner", description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--engine", default="altsa_sqlgen-phase1", choices=sorted(ENGINES))
    ap.add_argument("--cases", type=Path, default=CASES_ROOT)
    ap.add_argument("--only", default=None, help="substring filter on case id")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    cases = load_cases(args.cases, only=args.only)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 2
    engine = ENGINES[args.engine]
    results = run(args.url, engine, cases)
    print(report(results, engine.name))
    if args.json is not None:
        args.json.write_text(to_json(results, engine.name))

    bad = sum(
        1
        for r in results
        for c in r.columns
        if c.verdict in ("UNSOUND", "MISMATCH")
    ) + sum(1 for r in results if r.error is not None)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
