"""The generator's INTERNAL IR.

Deliberately separate from `altsa_runtime.catalog`: the runtime registry only
knows what the *executor* needs (kinds, nullability, ON clauses), while this IR
also carries everything the *renderer* needs (Python type strings, class names,
method names, shape trees). Keeping them apart means the runtime never has to
change when the renderer learns a new trick.

Every collection is a tuple and every construction site sorts, so a run is a
pure function of (schema, config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import final


@final
@dataclass(frozen=True, slots=True, order=True)
class GEnumMember:
    name: str
    value: str


@final
@dataclass(frozen=True, slots=True)
class GEnum:
    class_name: str
    members: tuple[GEnumMember, ...]
    db_name: str | None  # native PG type name, None for CHECK-derived enums
    native: bool


@final
@dataclass(frozen=True, slots=True)
class GColumn:
    name: str
    kind: str  # altsa_runtime.catalog kind
    py_type: str  # rendered Python annotation, e.g. "Decimal"
    numeric: bool  # -> NumCol instead of Col
    not_null: bool
    primary_key: bool
    has_default: bool  # server default or identity -> optional on INSERT
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    enum_name: str | None = None
    enum_values: tuple[str, ...] = ()
    item_kind: str | None = None
    source_type: str = ""  # reflected SQLAlchemy type, for the fidelity report


@final
@dataclass(frozen=True, slots=True)
class GTable:
    name: str
    camel: str  # "order_items" -> "OrderItems"
    columns: tuple[GColumn, ...]

    @property
    def cols_class(self) -> str:
        return f"_{self.camel}Cols"

    @property
    def cols_n_class(self) -> str:
        return f"_{self.camel}ColsN"

    @property
    def tag(self) -> str:
        return f"_T{self.camel}"

    @property
    def const(self) -> str:
        return self.name.upper()

    @property
    def const_n(self) -> str:
        return f"_{self.name.upper()}_N"


@final
@dataclass(frozen=True, slots=True)
class GEdge:
    id: str
    frm: str
    to: str
    on: tuple[tuple[str, str, str, str], ...]
    method_suffix: str  # -> join_<suffix> / left_join_<suffix>
    derived: bool  # True when auto-derived from a FK


@final
@dataclass(frozen=True, slots=True)
class GStep:
    edge: str
    target: str
    kind: str  # "inner" | "left"


@final
@dataclass(frozen=True, slots=True)
class GShape:
    root: str
    steps: tuple[GStep, ...]
    class_name: str
    declared: bool  # False => auto-generated single-edge shape (or a root)

    @property
    def key(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        return (self.root, tuple((s.edge, s.kind) for s in self.steps))

    def nullability(self) -> dict[str, bool]:
        """table -> is the alias on the nullable side of a LEFT JOIN."""
        out = {self.root: False}
        left_seen = False
        for s in self.steps:
            if s.kind == "left":
                left_seen = True
            out[s.target] = left_seen or s.kind == "left"
        return out


@final
@dataclass(frozen=True, slots=True)
class GProjField:
    field: str
    table: str
    column: str
    py_type: str


@final
@dataclass(frozen=True, slots=True)
class GProjection:
    row_class: str
    method: str
    shape_class: str
    fields: tuple[GProjField, ...]


@final
@dataclass(frozen=True, slots=True)
class GSchema:
    dialect: str
    enums: tuple[GEnum, ...]
    tables: tuple[GTable, ...]
    edges: tuple[GEdge, ...]
    shapes: tuple[GShape, ...]
    projections: tuple[GProjection, ...]
    warnings: tuple[str, ...] = field(default=())

    def table(self, name: str) -> GTable:
        for t in self.tables:
            if t.name == name:
                return t
        raise KeyError(name)

    def edge(self, eid: str) -> GEdge:
        for e in self.edges:
            if e.id == eid:
                return e
        raise KeyError(eid)
