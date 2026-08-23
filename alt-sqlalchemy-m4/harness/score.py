"""Score an engine over a corpus, reusing M3's scoring verbatim.

    uv run python harness/score.py --engine rust                     # no DB
    uv run python harness/score.py --engine rust --cases corpus-ext/cases
    uv run python harness/score.py --engine phase1 --url <dsn> --cases corpus-ext/cases

`rust` is M4's static analyser and needs no database. The other engine names are
M3's, and those do. Either way the verdicts come from `corpus.runner.score` and
the report from `corpus.runner.report`, so a number printed here means exactly
what the same number means in M3's evidence.

Exit code follows M3's runner: non-zero if anything is UNSOUND, MISMATCH or
ERROR.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine_rust import RustEngine
from m3link import M3_CASES_ROOT, M4_ROOT, m3_cases, m3_engines, m3_runner


def run_static(engine, cases) -> list:
    """M3's `runner.run` without the connection it opens for the DB engines."""
    results = []
    for case in cases:
        try:
            inferred = engine.analyze(None, case)
        except Exception as exc:  # noqa: BLE001 -- report, never crash the run
            if isinstance(exc, KeyboardInterrupt):
                raise
            results.append(
                m3_runner.CaseResult(
                    case=case, columns=(), error=f"{type(exc).__name__}: {exc}"
                )
            )
            continue
        results.append(
            m3_runner.CaseResult(case=case, columns=m3_runner.score(case, inferred))
        )
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness/score.py", description=__doc__)
    ap.add_argument("--engine", default="rust")
    ap.add_argument("--cases", type=Path, default=M3_CASES_ROOT)
    ap.add_argument("--url", default=None, help="required for the M3 engines only")
    ap.add_argument("--only", default=None)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--warnings", type=Path, default=None)
    ap.add_argument("--binary", type=Path, default=None)
    args = ap.parse_args(argv)

    cases_root = args.cases
    if not cases_root.is_absolute():
        cases_root = (M4_ROOT / cases_root).resolve()
    cases = m3_cases.load_cases(cases_root, only=args.only)
    if not cases:
        print(f"no cases found under {cases_root}", file=sys.stderr)
        return 2

    if args.engine == "rust":
        engine = RustEngine(binary=args.binary) if args.binary else RustEngine()
        results = run_static(engine, cases)
    else:
        if args.url is None:
            print(f"engine {args.engine!r} needs --url", file=sys.stderr)
            return 2
        if args.engine not in m3_engines.ENGINES:
            print(
                f"unknown engine {args.engine!r}; known: rust, "
                f"{', '.join(sorted(m3_engines.ENGINES))}",
                file=sys.stderr,
            )
            return 2
        engine = m3_engines.ENGINES[args.engine]
        results = m3_runner.run(args.url, engine, cases)

    print(m3_runner.report(results, engine.name))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(m3_runner.to_json(results, engine.name))
    if args.warnings is not None and isinstance(engine, RustEngine):
        lines = ["ENGINE WARNINGS", "=" * 72, ""]
        for case_id in sorted(engine.warnings or {}):
            lines.append(case_id)
            for w in (engine.warnings or {})[case_id]:
                lines.append(f"    {w}")
            lines.append("")
        if len(lines) == 3:
            lines.append("(none)")
        args.warnings.parent.mkdir(parents=True, exist_ok=True)
        args.warnings.write_text("\n".join(lines) + "\n")

    bad = sum(
        1 for r in results for c in r.columns if c.verdict in ("UNSOUND", "MISMATCH")
    ) + sum(1 for r in results if r.error is not None)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
