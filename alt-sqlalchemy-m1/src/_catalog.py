"""Schema catalog + declared join graph -- the generator's INPUT, kept as data.

`facade.py` is what a template turns this into. `_runtime.py` turns the same
data into SQLAlchemy Core `Table` objects, so the typed surface and the
executed SQL provably come from one source. No SQLAlchemy import here: this
module is pure data and stays importable by anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, final

PyKind = Literal["uuid", "text", "enum", "timestamptz", "numeric", "int"]


@final
@dataclass(frozen=True, slots=True)
class ColumnDef:
    name: str
    kind: PyKind
    not_null: bool
    primary_key: bool = False


@final
@dataclass(frozen=True, slots=True)
class TableDef:
    name: str
    columns: tuple[ColumnDef, ...]


@final
@dataclass(frozen=True, slots=True)
class JoinEdge:
    """A DECLARED edge. Only these become typed combinators in the facade."""

    frm: str
    to: str
    # (left_table, left_col, right_table, right_col) -- the ON clause
    on: tuple[str, str, str, str]


TABLES: Final[dict[str, TableDef]] = {
    "users": TableDef(
        "users",
        (
            ColumnDef("id", "uuid", not_null=True, primary_key=True),
            ColumnDef("email", "text", not_null=True),
            ColumnDef("status", "enum", not_null=True),
            ColumnDef("created_at", "timestamptz", not_null=True),
        ),
    ),
    "orders": TableDef(
        "orders",
        (
            ColumnDef("id", "uuid", not_null=True, primary_key=True),
            ColumnDef("user_id", "uuid", not_null=True),
            ColumnDef("status", "enum", not_null=True),
            ColumnDef("total", "numeric", not_null=True),
            ColumnDef("version", "int", not_null=True),
            ColumnDef("created_at", "timestamptz", not_null=True),
        ),
    ),
    "order_items": TableDef(
        "order_items",
        (
            ColumnDef("order_id", "uuid", not_null=True, primary_key=True),
            ColumnDef("product_id", "uuid", not_null=True, primary_key=True),
            ColumnDef("quantity", "int", not_null=True),
            ColumnDef("unit_price", "numeric", not_null=True),
        ),
    ),
}

JOIN_GRAPH: Final[tuple[JoinEdge, ...]] = (
    JoinEdge("orders", "users", ("users", "id", "orders", "user_id")),
    JoinEdge("orders", "order_items", ("order_items", "order_id", "orders", "id")),
    # The reverse traversal of the FK. A generator derives this for free from
    # the same constraint, and it is what lets `users` be a driving table.
    JoinEdge("users", "orders", ("users", "id", "orders", "user_id")),
)

JOIN_BY_TARGET: Final[dict[str, JoinEdge]] = {e.to: e for e in JOIN_GRAPH}
