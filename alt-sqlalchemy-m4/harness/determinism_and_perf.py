"""R6: byte-identical output on repeated runs, and per-case wall time.

    uv run python harness/determinism_and_perf.py [--runs 5] [--repeat 20]

Determinism: every case is analysed `--runs` times and the raw stdout bytes are
compared. Anything that leaked a hash-map iteration order, a timestamp or a
pointer value into the JSON would show up here.

Perf: each case is analysed `--repeat` times in a row; the reported figure is
the best-of and the mean of the *process* wall time, so it includes fork/exec,
reading the schema, parsing the DDL, parsing the query and analysing it -- i.e.
what a caller actually pays, not a micro-benchmark of the analysis function.
"""

from __future__ import annotations

import argparse
import hashlib
import statistics
import subprocess
import sys
import time
from pathlib import Path

from engine_rust import DEFAULT_BINARY
from m3link import M3_CASES_ROOT, M4_CASES_ROOT


def all_cases() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for root in (M3_CASES_ROOT, M4_CASES_ROOT):
        for group in sorted(p for p in root.iterdir() if p.is_dir()):
            for case in sorted(p for p in group.iterdir() if p.is_dir()):
                out.append((f"{group.name}/{case.name}", case))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--repeat", type=int, default=20)
    ap.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    args = ap.parse_args(argv)

    cases = all_cases()
    print("DETERMINISM + PERF -- altsa-analyze")
    print("=" * 78)
    print(f"binary : {args.binary}")
    print(f"cases  : {len(cases)}   determinism runs: {args.runs}   perf repeats: {args.repeat}")
    print()

    unstable: list[str] = []
    rows: list[tuple[str, float, float, str]] = []

    for case_id, path in cases:
        cmd = [
            str(args.binary),
            "--schema",
            str(path / "schema.sql"),
            "--query",
            str(path / "query.sql"),
        ]
        digests = set()
        for _ in range(args.runs):
            proc = subprocess.run(cmd, capture_output=True)
            digests.add(hashlib.sha256(proc.stdout).hexdigest())
        if len(digests) != 1:
            unstable.append(case_id)

        timings: list[float] = []
        for _ in range(args.repeat):
            t0 = time.perf_counter()
            subprocess.run(cmd, capture_output=True)
            timings.append((time.perf_counter() - t0) * 1000.0)
        rows.append(
            (case_id, min(timings), statistics.mean(timings), sorted(digests)[0][:16])
        )

    print(f"{'case':<48}{'best ms':>9}{'mean ms':>9}  sha256[:16]")
    print("-" * 78)
    for case_id, best, mean, digest in rows:
        print(f"{case_id:<48}{best:>9.2f}{mean:>9.2f}  {digest}")

    bests = [r[1] for r in rows]
    means = [r[2] for r in rows]
    print("-" * 78)
    print(f"process wall time, best-of-{args.repeat}: "
          f"min {min(bests):.2f} ms, median {statistics.median(bests):.2f} ms, "
          f"max {max(bests):.2f} ms")
    print(f"process wall time, mean               : "
          f"min {min(means):.2f} ms, median {statistics.median(means):.2f} ms, "
          f"max {max(means):.2f} ms")
    print()

    # Analysis-only time, straight from the binary, for the largest case.
    slowest = max(rows, key=lambda r: r[1])[0]
    path = dict(cases)[slowest]
    proc = subprocess.run(
        [
            str(args.binary),
            "--schema",
            str(path / "schema.sql"),
            "--query",
            str(path / "query.sql"),
            "--timing",
        ],
        capture_output=True,
        text=True,
    )
    print(f"in-process breakdown for the slowest case ({slowest}):")
    print(f"    {proc.stderr.strip()}")
    print()

    if unstable:
        print(f"NON-DETERMINISTIC ({len(unstable)}): {', '.join(unstable)}")
        print("FAIL")
        return 1
    print(f"DETERMINISM: PASS -- {len(cases)} cases x {args.runs} runs, "
          f"byte-identical stdout every time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
