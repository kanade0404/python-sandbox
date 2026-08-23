"""M3's live-database oracle, scoring the M4 Rust engine's answers.

    uv run python harness/oracle.py --url <dsn> [--cases <root>]

The asymmetric property is M3's and is checked by M3's code
(`corpus.oracle.observe`): no column the engine called NOT NULL may ever hold a
NULL in an actual result row. What is new here is only *whose* claims are on
trial -- the Rust analyser's, which never saw the database it is being checked
against.

Exit code is non-zero on any violation or error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from engine_rust import RustEngine
from m3link import M3_CASES_ROOT, M4_ROOT, m3_cases, m3_engines, m3_oracle


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness/oracle.py", description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--engine", default="rust")
    ap.add_argument("--cases", type=Path, default=M3_CASES_ROOT)
    ap.add_argument("--only", default=None)
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
    else:
        engine = m3_engines.ENGINES[args.engine]

    observations = m3_oracle.observe(args.url, engine, cases)
    print(m3_oracle.report(observations, engine.name))
    bad = sum(len(o.violations) for o in observations) + sum(
        1 for o in observations if o.error is not None
    )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
