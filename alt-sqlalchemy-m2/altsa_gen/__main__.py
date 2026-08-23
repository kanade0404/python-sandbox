"""CLI:  uv run python -m altsa_gen --url <db-url> --config joins.toml --out <dir>"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .frontend import build_schema
from .render import render


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="altsa_gen")
    ap.add_argument("--url", required=True, help="SQLAlchemy database URL")
    ap.add_argument("--config", required=True, type=Path, help="joins.toml")
    ap.add_argument("--out", required=True, type=Path, help="output package dir")
    ap.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write a human-readable generation report here",
    )
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    schema = build_schema(args.url, cfg)
    code = render(schema)

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    init = out_dir / "__init__.py"
    if not init.exists():
        init.write_text("")
    target = out_dir / f"{cfg.module}.py"
    target.write_text(code)

    lines = code.count("\n")
    report: list[str] = [
        f"dialect      : {schema.dialect}",
        f"tables       : {len(schema.tables)}",
        f"enums        : {len(schema.enums)}",
        f"edges        : {len(schema.edges)} "
        f"({sum(1 for e in schema.edges if e.derived)} derived from FKs)",
        f"shapes       : {len(schema.shapes)} "
        f"({sum(1 for s in schema.shapes if s.declared)} declared multi-edge)",
        f"projections  : {len(schema.projections)}",
        f"output       : {target} ({lines} lines)",
        "",
        "tables:",
    ]
    for t in schema.tables:
        cols = ", ".join(
            f"{c.name}:{c.kind}{'' if c.not_null else '?'}" for c in t.columns
        )
        report.append(f"  {t.name}({cols})")
    report.append("")
    report.append("edges:")
    for e in schema.edges:
        on = " AND ".join(f"{a}.{b} = {c}.{d}" for (a, b, c, d) in e.on)
        report.append(f"  {e.id}: {e.frm} -> {e.to} ON {on}")
    report.append("")
    report.append("shapes:")
    for s in schema.shapes:
        path = " ".join(f"{st.kind}:{st.target}" for st in s.steps)
        report.append(
            f"  {s.class_name:<40} {s.root} {path}"
            f"{'  [declared]' if s.declared else ''}"
        )
    if schema.warnings:
        report.append("")
        report.append("warnings:")
        report.extend(f"  {w}" for w in schema.warnings)
    text = "\n".join(report) + "\n"

    if args.report is not None:
        args.report.write_text(text)
    sys.stderr.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
