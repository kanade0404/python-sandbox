"""Render evidence/pyright_positive.txt and evidence/pyright_negative.txt."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
EV = ROOT / "evidence"


def _pyright(config: str) -> dict[str, object]:
    proc = subprocess.run(
        [
            "pyright",
            "--project",
            str(ROOT / config),
            "--outputjson",
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    return json.loads(proc.stdout)


def _rel(p: str) -> str:
    return str(Path(p).relative_to(ROOT))


def positive() -> None:
    d = _pyright("pyrightconfig.positive.json")
    diags = d["generalDiagnostics"]
    assert isinstance(diags, list)
    errors = [g for g in diags if g["severity"] == "error"]
    infos = [g for g in diags if g["severity"] == "information"]

    out: list[str] = []
    out.append("=" * 78)
    out.append("POSITIVE RUN -- pyright strict, must be 0 errors")
    out.append("=" * 78)
    out.append("command: uv run pyright --project pyrightconfig.positive.json")
    out.append("files  : src/facade.py src/_ir.py src/_catalog.py src/_runtime.py")
    out.append("         src/proof_positive.py src/demo_runtime.py")
    out.append(f"pyright: {d['version']}")
    out.append("")
    out.append(f"ERRORS: {len(errors)}")
    for g in errors:
        out.append(f"  {_rel(g['file'])}:{g['range']['start']['line'] + 1} {g['message']}")
    out.append("")
    out.append("=" * 78)
    out.append(f"reveal_type() PROBES -- {len(infos)} total")
    out.append("=" * 78)

    src = (ROOT / "src" / "proof_positive.py").read_text().splitlines()
    section = ""
    for g in infos:
        ln = g["range"]["start"]["line"] + 1
        new_section = _section_of(src, ln)
        if new_section != section:
            section = new_section
            out.append("")
            out.append(f"--- {section} " + "-" * max(0, 70 - len(section)))
        out.append(f"  L{ln:<4} {g['message']}")
    out.append("")
    _write(EV / "pyright_positive.txt", out)
    print(f"positive: {len(errors)} errors, {len(infos)} reveals")


def _section_of(src: list[str], ln: int) -> str:
    for i in range(ln - 1, -1, -1):
        s = src[i]
        if s.startswith("# C") or (s.startswith("# ") and s[2:3].isupper() and "--" in s):
            return s[2:].strip()
        if s.startswith("def ") and i < ln - 1:
            continue
    return "misc"


def negative() -> None:
    d = _pyright("pyrightconfig.negative.json")
    diags = d["generalDiagnostics"]
    assert isinstance(diags, list)
    errors = [g for g in diags if g["severity"] == "error"]
    src = (ROOT / "src" / "proof_negative.py").read_text().splitlines()

    by_line: dict[int, list[str]] = {}
    for g in errors:
        by_line.setdefault(g["range"]["start"]["line"] + 1, []).append(
            f"[{g.get('rule')}] {g['message'].splitlines()[0]}"
        )

    out: list[str] = []
    out.append("=" * 78)
    out.append("NEGATIVE RUN -- every case below MUST fail to type-check")
    out.append("=" * 78)
    out.append("command: uv run pyright --project pyrightconfig.negative.json")
    out.append("file   : src/proof_negative.py")
    out.append(f"pyright: {d['version']}")
    out.append("")
    out.append(f"EXPECTED CASES : 23  (one `# EXPECT-ERROR` marker each)")
    out.append(f"LINES IN ERROR : {len(by_line)}")
    out.append(f"TOTAL DIAGS    : {len(errors)}")
    out.append("")
    out.append("The diagnostic count exceeds the case count because pyright emits an")
    out.append("extra reportUnknownMemberType alongside each reportAttributeAccessIssue.")
    out.append("check_negative.py asserts the two-way match: every marked line errors,")
    out.append("and every error lands on a marked line.")
    out.append("")
    out.append("=" * 78)
    for ln in sorted(by_line):
        name = "?"
        for i in range(ln - 1, -1, -1):
            if src[i].startswith("def "):
                name = src[i][4:].split("(")[0]
                break
        code = src[ln - 1].strip()
        out.append("")
        out.append(f"{name}   (line {ln})")
        out.append(f"    code: {code}")
        for m in by_line[ln]:
            out.append(f"    {m}")
    out.append("")
    _write(EV / "pyright_negative.txt", out)
    print(f"negative: {len(errors)} diags on {len(by_line)} lines")


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    EV.mkdir(exist_ok=True)
    positive()
    negative()
