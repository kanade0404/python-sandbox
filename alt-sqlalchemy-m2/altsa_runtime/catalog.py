"""Schema registry: the seam between GENERATED code and the SQLAlchemy backend.

SCHEMA-INDEPENDENT machinery, SCHEMA-DEPENDENT contents. In M1 this module was
literally the schema (`_catalog.py` held `TABLES` as a hand-written dict). In M2
the generated facade *calls into* this registry at import time, so the runtime
package stays reusable across schemas.

No SQLAlchemy import here: this module is pure data and stays importable by
anything (including the "no leakage" checker).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final, final

# The runtime type kinds a generator may emit. `_backend` maps each to a
# SQLAlchemy TypeEngine and to a read-side decoder.
KINDS: Final[frozenset[str]] = frozenset(
    {
        "uuid",
        "text",
        "enum",
        "timestamptz",
        "timestamp",
        "date",
        "time",
        "numeric",
        "int",
        "float",
        "bool",
        "json",
        "bytes",
        "array",
        "opaque",
    }
)


@final
@dataclass(frozen=True, slots=True)
class ColumnSpec:
    name: str
    kind: str
    not_null: bool
    primary_key: bool = False
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    enum_name: str | None = None
    enum_values: tuple[str, ...] = ()
    item_kind: str | None = None
    has_default: bool = False


@final
@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]

    def column(self, name: str) -> ColumnSpec:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"{self.name}.{name}")


@final
@dataclass(frozen=True, slots=True)
class EdgeSpec:
    """A DECLARED join edge. Only these become typed combinators in a facade.

    `on` is a tuple of (left_table, left_col, right_table, right_col) pairs; all
    pairs are AND-ed. Composite foreign keys therefore work.
    """

    id: str
    frm: str
    to: str
    on: tuple[tuple[str, str, str, str], ...]


@final
class Registry:
    __slots__ = ("edges", "enums", "tables")

    def __init__(self) -> None:
        self.tables: dict[str, TableSpec] = {}
        self.edges: dict[str, EdgeSpec] = {}
        self.enums: dict[tuple[str, str], type[enum.Enum]] = {}

    def clear(self) -> None:
        self.tables.clear()
        self.edges.clear()
        self.enums.clear()


REGISTRY: Final[Registry] = Registry()


def register_table(spec: TableSpec) -> None:
    REGISTRY.tables[spec.name] = spec


def register_edge(spec: EdgeSpec) -> None:
    REGISTRY.edges[spec.id] = spec


def register_enum(table: str, column: str, cls: type[enum.Enum]) -> None:
    """Called by the generated facade once per enum-typed column."""
    REGISTRY.enums[table, column] = cls


def column_kind(table: str, column: str) -> str | None:
    t = REGISTRY.tables.get(table)
    if t is None:
        return None
    for c in t.columns:
        if c.name == column:
            return c.kind
    return None
