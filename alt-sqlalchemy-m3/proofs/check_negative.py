"""Verify the negative run matches proof_negative.py's `# EXPECT-ERROR` markers.

Passing means BOTH directions hold:
  * every marked line produced at least one pyright error  (nothing silently
    type-checks that must not)
  * every pyright error landed on a marked line             (no incidental
    errors are being counted as evidence)

Usage:
  uv run pyright --project proofs/pyrightconfig.negative.json --outputjson \
    | uv run python proofs/check_negative.py
"""

from __future__ import annotations

import json
import re
import sys
import tokenize
from collections import defaultdict
from pathlib import Path

SRC = Path(__file__).parent / "proof_negative.py"
MARK = re.compile(r"#\s*EXPECT-ERROR")


def main() -> int:
    lines = SRC.read_text().splitlines()
    # Tokenize rather than grep: the module docstring mentions the marker in
    # prose, and only real COMMENT tokens should count as markers.
    expected: set[int] = set()
    with SRC.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.COMMENT and MARK.search(tok.string):
                expected.add(tok.start[0])

    report = json.load(sys.stdin)
    errors = [d for d in report["generalDiagnostics"] if d["severity"] == "error"]

    by_line: dict[int, list[str]] = defaultdict(list)
    for d in errors:
        by_line[d["range"]["start"]["line"] + 1].append(
            f"[{d.get('rule')}] {d['message'].splitlines()[0]}"
        )

    got = set(by_line)
    missing = sorted(expected - got)
    unexpected = sorted(got - expected)

    print(f"EXPECT-ERROR markers : {len(expected)}")
    print(f"lines with errors    : {len(got)}")
    print(f"total diagnostics    : {len(errors)}")
    print()

    for ln in sorted(expected):
        name = _enclosing_case(lines, ln)
        msgs = by_line.get(ln, [])
        status = "OK  " if msgs else "MISS"
        print(f"{status} L{ln:<4} {name}")
        for m in msgs:
            print(f"        {m}")

    print()
    if missing:
        print(f"FAILURE: {len(missing)} marked line(s) produced NO error: {missing}")
    if unexpected:
        print(f"FAILURE: error(s) on unmarked line(s): {unexpected}")
        for ln in unexpected:
            for m in by_line[ln]:
                print(f"    L{ln}: {m}")
    if not missing and not unexpected:
        print(
            f"PASS: all {len(expected)} negative cases produced errors "
            f"({len(errors)} diagnostics total; pyright may emit more than one "
            f"per line)"
        )
        return 0
    return 1


def _enclosing_case(lines: list[str], ln: int) -> str:
    for i in range(ln - 1, -1, -1):
        s = lines[i]
        if s.startswith("def "):
            return s[4:].split("(")[0]
    return "?"


if __name__ == "__main__":
    sys.exit(main())
