"""CLI: `uv run python -m altsa_sqlgen --url <dsn> --queries <dir> --out <dir>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .errors import GenerationError
from .generate import generate


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="altsa_sqlgen", description=__doc__)
    ap.add_argument("--url", required=True, help="PostgreSQL DSN to describe against")
    ap.add_argument("--queries", required=True, type=Path, help="directory of annotated .sql")
    ap.add_argument("--out", required=True, type=Path, help="output package directory")
    ap.add_argument(
        "--report", action="store_true", help="print a per-column inference report"
    )
    args = ap.parse_args(argv)

    try:
        result = generate(url=args.url, queries=args.queries, out=args.out)
    except GenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for path in result.files:
        print(f"wrote {path}")
    print(
        f"{len(result.described)} quer(ies) in {len(result.modules)} module(s), "
        f"{sum(len(d.columns) for d in result.described)} result column(s)"
    )

    if args.report:
        print()
        for d in result.described:
            print(f"-- {d.block.name} :{d.block.kind}")
            for p in d.params:
                print(f"   param ${p.index} {p.name}: {p.py.text}")
            for c in d.columns:
                print(
                    f"   col {c.field:<24} {c.py.text:<28} "
                    f"catalog={_fmt(c.catalog)} explain={_fmt(c.explain)} "
                    f"override={c.override or '-'} -> {'NULL' if c.nullable else 'NOT NULL'}"
                )
    return 0


def _fmt(value: bool | None) -> str:
    return "?" if value is None else ("T" if value else "F")


if __name__ == "__main__":
    sys.exit(main())
