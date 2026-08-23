"""Loading and applying corpus cases. Shared by `runner.py` and `oracle.py`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import psycopg

from altsa_sqlgen.annotations import QueryBlock, parse_file

Expectation = bool | Literal["unknown"]

CASES_ROOT = Path(__file__).parent / "cases"


@dataclass(frozen=True, slots=True)
class ExpectedColumn:
    name: str
    nullable: Expectation

    @property
    def must_be_nullable(self) -> bool:
        """`"unknown"` is scored exactly like `true`: claiming NOT NULL is wrong."""
        return self.nullable is not False


@dataclass(frozen=True, slots=True)
class Case:
    group: str
    name: str
    path: Path
    schema: str
    query: str
    expected: tuple[ExpectedColumn, ...]
    provenance: str
    notes: str
    seed: str | None

    @property
    def id(self) -> str:
        return f"{self.group}/{self.name}"

    def block(self) -> QueryBlock:
        """Wrap the bare SQL in the annotation the generator's parser expects."""
        text = f"-- QUERY corpus_{self.name} :many\n{self.query}"
        return parse_file(text, source=f"{self.id}/query.sql")[0]


def load_cases(root: Path = CASES_ROOT, *, only: str | None = None) -> list[Case]:
    """Every case under `root`, sorted by id so runs are reproducible."""
    cases: list[Case] = []
    for group_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for case_dir in sorted(p for p in group_dir.iterdir() if p.is_dir()):
            case = _load_one(group_dir.name, case_dir)
            if only is None or only in case.id:
                cases.append(case)
    return cases


def _load_one(group: str, path: Path) -> Case:
    # `json.loads` is `Any` by construction; this cast is the single boundary
    # where the fixture re-enters the type system, and every field below is
    # validated rather than trusted.
    parsed = cast("object", json.loads((path / "expected.json").read_text()))
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}/expected.json must be an object")
    raw = cast("dict[str, object]", parsed)

    columns_raw = raw.get("columns")
    if not isinstance(columns_raw, list) or not columns_raw:
        raise ValueError(f"{path}/expected.json needs a non-empty `columns` array")
    columns: list[ExpectedColumn] = []
    for entry in cast("list[object]", columns_raw):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}/expected.json: each column must be an object")
        column = cast("dict[str, object]", entry)
        name = column.get("name")
        nullable = column.get("nullable")
        if not isinstance(name, str):
            raise ValueError(f"{path}/expected.json: column needs a string `name`")
        expectation: Expectation
        if nullable is True or nullable is False:
            expectation = nullable
        elif nullable == "unknown":
            expectation = "unknown"
        else:
            raise ValueError(
                f"{path}/expected.json: column {name!r} `nullable` must be "
                f'true, false or "unknown" (got {nullable!r})'
            )
        columns.append(ExpectedColumn(name=name, nullable=expectation))

    provenance = raw.get("provenance")
    if not isinstance(provenance, str) or not provenance:
        raise ValueError(f"{path}/expected.json needs a non-empty `provenance`")
    notes = raw.get("notes")
    seed_path = path / "seed.sql"
    return Case(
        group=group,
        name=path.name,
        path=path,
        schema=(path / "schema.sql").read_text(),
        query=(path / "query.sql").read_text().strip(),
        expected=tuple(columns),
        provenance=provenance,
        notes=notes if isinstance(notes, str) else "",
        seed=seed_path.read_text() if seed_path.exists() else None,
    )


def apply_schema(conn: psycopg.Connection[tuple[object, ...]], case: Case) -> None:
    """Reset `public` to empty, then apply the case's DDL.

    Each case owns its schema; resetting keeps them from leaking into each
    other and makes the run order irrelevant.
    """
    conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
    conn.execute("CREATE SCHEMA public")
    conn.execute("SET search_path = public")
    # Fixture DDL is not a `LiteralString`; bytes is psycopg's escape hatch and
    # marks the boundary explicitly. See describe.py for the same argument.
    conn.execute(case.schema.encode())


def connect(url: str) -> psycopg.Connection[tuple[object, ...]]:
    return psycopg.connect(url, autocommit=True)


def iter_pairs(
    case: Case, engine_columns: list[str]
) -> Iterator[tuple[int, ExpectedColumn | None, str | None]]:
    """Zip expectation against engine output, tolerating a length mismatch."""
    for i in range(max(len(case.expected), len(engine_columns))):
        exp = case.expected[i] if i < len(case.expected) else None
        got = engine_columns[i] if i < len(engine_columns) else None
        yield i, exp, got
