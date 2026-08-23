"""Import M3's corpus machinery without modifying a byte of M3.

M3's `corpus/` package holds the fixture loader, the scoring rules and the
oracle. Re-implementing them here would let the two milestones drift, and the
whole point of the corpus is that both engines are scored by the *same* code.
So this module puts M3's tree on `sys.path` and imports it read-only.

Only `corpus.engines` is *not* re-used wholesale: its `ENGINES` dict is a fixed
registry of the three Phase 1 engines, and `--engine` in M3's CLI is bounded by
it. The `Engine` protocol it defines is structural, so the Rust engine in
`engine_rust.py` satisfies it without M3 knowing anything about it, and M3's
`runner.run` / `oracle.observe` accept it directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

# M3 is audited evidence and must not be written to. Importing a package
# normally drops `.pyc` files into its `__pycache__`; this stops that for every
# import made from here on, at the cost of re-compiling M3's five modules on
# each run (single-digit milliseconds).
sys.dont_write_bytecode = True

HARNESS_DIR = Path(__file__).resolve().parent
M4_ROOT = HARNESS_DIR.parent
REPO_ROOT = M4_ROOT.parent
M3_ROOT = REPO_ROOT / "alt-sqlalchemy-m3"

if not (M3_ROOT / "corpus" / "runner.py").exists():  # pragma: no cover - setup guard
    raise SystemExit(f"cannot find M3's corpus at {M3_ROOT}")

if str(M3_ROOT) not in sys.path:
    sys.path.insert(0, str(M3_ROOT))

# Re-exported so callers never have to think about the path juggling.
from corpus import cases as m3_cases  # noqa: E402
from corpus import engines as m3_engines  # noqa: E402
from corpus import oracle as m3_oracle  # noqa: E402
from corpus import runner as m3_runner  # noqa: E402

M3_CASES_ROOT = M3_ROOT / "corpus" / "cases"
M4_CASES_ROOT = M4_ROOT / "corpus-ext" / "cases"

__all__ = [
    "M3_ROOT",
    "M4_ROOT",
    "M3_CASES_ROOT",
    "M4_CASES_ROOT",
    "m3_cases",
    "m3_engines",
    "m3_oracle",
    "m3_runner",
]
