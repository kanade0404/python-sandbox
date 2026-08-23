"""G2, stated mechanically: does the GENERATED facade reveal the SAME types
M1's HAND-WRITTEN facade did?

Parses `Type of "<expr>" is "<type>"` out of both pyright runs and compares the
expressions the two proof files have in common (M2's proof is M1's, re-pointed
and extended, so the shared set is all of M1's claims that survive the wider
schema).

Usage:
    uv run python proofs/compare_m1_reveals.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RE = re.compile(r'Type of "(.*?)" is "(.*)"$')

HERE = Path(__file__).resolve().parent
M1 = HERE.parent.parent / "alt-sqlalchemy-m1" / "evidence" / "pyright_positive.txt"
M2 = HERE.parent / "evidence" / "pyright_positive.txt"


def load(p: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in p.read_text().splitlines():
        m = RE.search(line.strip())
        if m:
            out.setdefault(m.group(1), m.group(2))
    return out


def main() -> int:
    m1 = load(M1)
    m2 = load(M2)
    shared = sorted(set(m1) & set(m2))
    diff = [k for k in shared if m1[k] != m2[k]]
    missing = sorted(set(m1) - set(m2))

    print(f"M1 reveal_type expressions : {len(m1)}")
    print(f"M2 reveal_type expressions : {len(m2)}")
    print(f"present in BOTH            : {len(shared)}")
    print(f"  identical revealed type  : {len(shared) - len(diff)}")
    print(f"  differing                : {len(diff)}")
    print(f"M1 expressions not in M2   : {len(missing)}")
    print()
    for k in shared:
        print(f'  OK  {k}\n        {m2[k]}')
    for k in diff:
        print(f"  DIFF {k}\n        M1: {m1[k]}\n        M2: {m2[k]}")
    for k in missing:
        print(f"  GONE {k} -> {m1[k]}")
    print()
    ok = not diff and not missing
    print(
        "G2 EQUIVALENCE: "
        + (
            "PASS -- every shared claim reveals the same type"
            if ok
            else "FAIL"
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
