"""Run the runtime halves of the positive proof (H2's leakage clause).

    uv run python proofs/run_checks.py

Pyright proves the generated code type-checks. This proves the two properties
that a type checker cannot see from inside: that no generated public signature
mentions `Any`, and that none of them exposes a psycopg type other than
`Connection`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proofs.proof_positive import (  # noqa: E402
    assert_no_any_in_source,
    assert_no_leakage,
)


def main() -> int:
    leaks = assert_no_leakage()
    anys = assert_no_any_in_source()
    ok = not leaks and not anys
    print("H2 leakage clause: PASS" if ok else "H2 leakage clause: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
