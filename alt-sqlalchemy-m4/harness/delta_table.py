"""Emit the Phase 1 vs Phase 2 side-by-side table, from the recorded JSON.

    uv run python harness/delta_table.py > evidence/phase1_vs_phase2.md

Both columns come from the same scoring code (M3's `corpus.runner.score`), run
over the same fixtures, so the table is a measurement rather than a claim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parent.parent / "evidence"

PAIRS = [
    ("corpus-ext (11 cases, 29 columns)", "scores_phase1_corpusext.json", "scores_rust_corpusext.json"),
    ("M3 corpus (26 cases, 65 columns)", "scores_phase1_m3corpus.json", "scores_rust_m3corpus.json"),
]

SHORT = {
    "PASS": "PASS",
    "SAFE-FALSE-POSITIVE": "safe-FP",
    "UNSOUND": "UNSOUND",
    "MISMATCH": "MISMATCH",
    "ERROR": "ERROR",
}


def load(name: str) -> dict[str, list[dict]]:
    payload = json.loads((EVIDENCE / name).read_text())
    return {c["id"]: c["columns"] for c in payload["cases"]}


def main() -> int:
    print("# Phase 1 vs Phase 2, column by column")
    print()
    print("Both columns are produced by M3's `corpus.runner.score` over the same")
    print("fixtures; the only thing that differs is the engine.")
    print()
    print("* **Phase 1** = `altsa_sqlgen-phase1` (M3): libpq DESCRIBE for names and")
    print("  base-table attribution, `pg_attribute.attnotnull` for the catalog answer,")
    print("  `EXPLAIN (VERBOSE, FORMAT JSON)` walked for outer-join upgrades. Needs a")
    print("  live server.")
    print("* **Phase 2** = `altsa-analyze` (M4): DDL + parse tree, no server.")
    print()

    for title, phase1_file, phase2_file in PAIRS:
        p1 = load(phase1_file)
        p2 = load(phase2_file)
        print(f"## {title}")
        print()
        print("| case | column | correct | Phase 1 | Phase 2 | delta |")
        print("|---|---|---|---|---|---|")
        gained = 0
        for case_id in sorted(p2):
            for a, b in zip(p2[case_id], p1.get(case_id, [])):
                delta = ""
                if a["verdict"] != b["verdict"]:
                    delta = "**gained**"
                    gained += 1
                print(
                    f'| `{case_id}` | `{a["name"]}` | {a["expected"]} '
                    f'| {b["got"]} ({SHORT.get(b["verdict"], b["verdict"])}) '
                    f'| {a["got"]} ({SHORT.get(a["verdict"], a["verdict"])}) | {delta} |'
                )
        n1 = sum(1 for cols in p1.values() for c in cols if c["verdict"] == "SAFE-FALSE-POSITIVE")
        n2 = sum(1 for cols in p2.values() for c in cols if c["verdict"] == "SAFE-FALSE-POSITIVE")
        u1 = sum(1 for cols in p1.values() for c in cols if c["verdict"] == "UNSOUND")
        u2 = sum(1 for cols in p2.values() for c in cols if c["verdict"] == "UNSOUND")
        total = sum(len(cols) for cols in p2.values())
        print()
        print(
            f"**{title}**: {total} columns. Phase 1 {n1} safe-FP / {u1} unsound; "
            f"Phase 2 {n2} safe-FP / {u2} unsound. "
            f"{gained} columns move from safe-FP to PASS."
        )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
