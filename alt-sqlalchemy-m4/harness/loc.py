"""Line-count breakdown for the milestone report.

    python3 harness/loc.py > evidence/loc.txt
"""

from __future__ import annotations

from pathlib import Path

M4 = Path(__file__).resolve().parent.parent

RUST = [
    ("catalog.rs", "DDL -> catalog"),
    ("scope.rs", "bind_from + column resolution"),
    ("expr.rs", "expression nullability lattice"),
    ("output.rs", "projection, star, CTEs, set ops"),
    ("funcs.rs", "function-table loader"),
    ("params.rs", "${name} -> $n scanner"),
    ("main.rs", "CLI, JSON, integration tests"),
]
DATA = [("functions.toml", "the function table itself")]
HARNESS = [
    ("m3link.py", "import M3's corpus package, read-only"),
    ("engine_rust.py", "Rust engine as an M3 Engine"),
    ("score.py", "runner"),
    ("oracle.py", "live-DB oracle"),
    ("determinism_and_perf.py", "R6"),
    ("delta_table.py", "Phase1 vs Phase2 table"),
    ("loc.py", "this file"),
]


def counts(path: Path, comment_prefix: str) -> tuple[int, int, int, int]:
    lines = path.read_text().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    blank = sum(1 for line in lines if not line.strip())
    comment = sum(1 for line in lines if line.strip().startswith(comment_prefix))
    return len(lines), blank, comment, len(lines) - blank - comment


def test_lines(path: Path) -> int:
    text = path.read_text()
    i = text.find("#[cfg(test)]")
    return 0 if i < 0 else text[i:].count("\n") + 1


def section(title: str, base: Path, entries: list[tuple[str, str]], prefix: str) -> tuple[int, int]:
    print(title)
    print(f"  {'file':<26}{'total':>7}{'blank':>7}{'comment':>9}{'code':>7}   what")
    tot = code = 0
    for name, what in entries:
        t, b, c, k = counts(base / name, prefix)
        tot += t
        code += k
        print(f"  {name:<26}{t:>7}{b:>7}{c:>9}{k:>7}   {what}")
    print(f"  {'':<26}{'-'*7}{'':>7}{'':>9}{'-'*7}")
    print(f"  {'subtotal':<26}{tot:>7}{'':>7}{'':>9}{code:>7}")
    print()
    return tot, code


def main() -> None:
    print("altsa-analyze -- line counts")
    print("=" * 72)
    print()
    r_tot, r_code = section("Rust (src/*.rs)", M4 / "src", RUST, "//")
    d_tot, d_code = section("Data (src/*.toml, embedded)", M4 / "src", DATA, "#")
    h_tot, h_code = section("Python harness (harness/*.py)", M4 / "harness", HARNESS, "#")

    tests = sum(test_lines(M4 / "src" / n) for n, _ in RUST)
    print(f"Rust total                 : {r_tot} lines ({r_code} code)")
    print(f"  of which #[cfg(test)]    : {tests} lines")
    print(f"  non-test Rust            : {r_tot - tests} lines")
    print(f"Function table (data)      : {d_tot} lines ({d_code} entries+syntax)")
    print(f"ENGINE TOTAL (rust + data) : {r_tot + d_tot} lines")
    print()
    print(f"Python harness             : {h_tot} lines ({h_code} code)")
    print()
    print("Estimate in the M4 brief   : 2,500 - 3,500 lines of Rust")
    print(f"Actual                     : {r_tot + d_tot} "
          f"({r_tot - tests} non-test Rust + {tests} test + {d_tot} data)")


if __name__ == "__main__":
    main()
