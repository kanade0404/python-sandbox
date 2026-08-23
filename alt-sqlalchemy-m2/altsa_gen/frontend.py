"""Reflection + normalisation frontend.

REUSED FROM sqlacodegen (the whole point of depending on it):
  * `TablesGenerator.fix_column_types()` -- turns CHECK-constrained VARCHARs
    into synthetic `Enum`s, `IN (0,1)` integers into `Boolean`, and registers
    native PostgreSQL ENUMs; also normalises dialect-specific types towards the
    generic SQLAlchemy ones via `get_adapted_type()`.
  * `TablesGenerator.enum_classes` / `.enum_values` -- the (table, column) ->
    Python-enum-class-name mapping and its members, including sqlacodegen's
    de-duplication of enums shared by several columns.
  * `TablesGenerator._enum_name_to_class_name()` and `should_ignore_table()`.

WRITTEN HERE: everything downstream. sqlacodegen's renderers emit SQLAlchemy
models; we never call them. `AltsaFrontend.normalize()` runs exactly the first
half of `TablesGenerator.generate()` and then stops.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from keyword import iskeyword
from typing import Any, cast, final

import sqlalchemy
from sqlacodegen.generators import TablesGenerator
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Time,
    Uuid,
    create_engine,
)
from sqlalchemy.sql.schema import Column, ForeignKeyConstraint
from sqlalchemy.types import TypeEngine

from .config import Config, ShapeDecl
from .gir import (
    GColumn,
    GEdge,
    GEnum,
    GEnumMember,
    GProjection,
    GProjField,
    GSchema,
    GShape,
    GStep,
    GTable,
)

_RE_INVALID = re.compile(r"[^a-zA-Z0-9_]")


def camel(name: str) -> str:
    return "".join(p.capitalize() for p in name.split("_") if p)


def enum_member_name(value: str) -> str:
    """Same derivation sqlacodegen uses for enum members (generators.py:914)."""
    unescaped = value.replace("\\'", "'").replace("\\\\", "\\")
    member = _RE_INVALID.sub("_", unescaped).upper()
    if not member:
        member = "EMPTY"
    elif member[0].isdigit():
        member = "_" + member
    elif iskeyword(member.lower()):
        member += "_"
    return member


@final
class AltsaFrontend(TablesGenerator):
    """Runs sqlacodegen's normalisation, then hands the metadata back."""

    def normalize(self) -> None:
        self.generate_base()
        for table in list(self.metadata.tables.values()):
            if self.should_ignore_table(table):
                self.metadata.remove(table)
        for table in self.metadata.tables.values():
            self.fix_column_types(table)


# ---------------------------------------------------------------------------
# type classification
# ---------------------------------------------------------------------------


def _classify(
    table: Table, column: Column[Any], enum_class: str | None, warnings: list[str]
) -> GColumn:
    t = column.type
    source = f"{t.__class__.__name__}"
    kind = "opaque"
    py = "object"
    numeric = False
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    enum_name: str | None = None
    enum_values: tuple[str, ...] = ()
    item_kind: str | None = None

    if enum_class is not None and isinstance(t, Enum):
        kind = "enum"
        py = enum_class
        enum_values = tuple(t.enums)
        enum_name = t.name if t.native_enum else None
    elif isinstance(t, Uuid):
        kind, py = "uuid", "UUID"
    elif isinstance(t, Boolean):
        kind, py = "bool", "bool"
    elif isinstance(t, Float):
        kind, py, numeric = "float", "float", True
    elif isinstance(t, Numeric):
        kind, py, numeric = "numeric", "Decimal", True
        precision, scale = t.precision, t.scale
    elif isinstance(t, Integer):
        kind, py, numeric = "int", "int", True
    elif isinstance(t, DateTime):
        kind = "timestamptz" if t.timezone else "timestamp"
        py = "_dt.datetime"
    elif isinstance(t, Date):
        kind, py = "date", "_dt.date"
    elif isinstance(t, Time):
        kind, py = "time", "_dt.time"
    elif isinstance(t, JSON):
        kind, py = "json", "object"
    elif isinstance(t, ARRAY):
        # `ARRAY.item_type` is `TypeEngine[Unknown]` in SQLAlchemy's stubs; the
        # double cast keeps pyright strict happy without an ignore comment.
        item_type = cast("TypeEngine[Any]", cast("Any", t).item_type)
        inner = _classify(table, Column(column.name, item_type), None, warnings)
        kind, py, item_kind = "array", f"list[{inner.py_type}]", inner.kind
    elif isinstance(t, LargeBinary):
        kind, py = "bytes", "bytes"
    elif isinstance(t, String):
        kind, py = "text", "str"
        length = t.length
    else:
        warnings.append(
            f"{table.name}.{column.name}: unmapped SQL type {source!r}; "
            f"emitted as opaque `object`"
        )

    has_default = column.server_default is not None or column.default is not None

    return GColumn(
        name=column.name,
        kind=kind,
        py_type=py,
        numeric=numeric,
        not_null=not column.nullable,
        primary_key=bool(column.primary_key),
        has_default=has_default,
        length=length,
        precision=precision,
        scale=scale,
        enum_name=enum_name,
        enum_values=enum_values,
        item_kind=item_kind,
        source_type=source,
    )


# ---------------------------------------------------------------------------
# edges
# ---------------------------------------------------------------------------


def _fk_edges(metadata: MetaData, tables: Sequence[str]) -> list[GEdge]:
    """Both directions of every single- or multi-column foreign key."""
    raw: list[tuple[str, str, tuple[tuple[str, str, str, str], ...], str]] = []
    for tname in sorted(tables):
        table = metadata.tables[tname]
        constraints = [
            c for c in table.constraints if isinstance(c, ForeignKeyConstraint)
        ]
        for con in sorted(constraints, key=lambda c: sorted(c.column_keys)):
            pairs: list[tuple[str, str, str, str]] = []
            for fk in con.elements:
                parent = fk.column
                child = fk.parent
                if parent.table.name not in tables:
                    break
                pairs.append(
                    (parent.table.name, parent.name, child.table.name, child.name)
                )
            if not pairs:
                continue
            pairs.sort()
            parent_table = pairs[0][0]
            child_cols = "_".join(p[3] for p in pairs)
            raw.append((tname, parent_table, tuple(pairs), child_cols))

    # both directions; disambiguate ids/method names when a pair repeats
    by_pair: dict[tuple[str, str], int] = {}
    for child, parent, _pairs, _cols in raw:
        by_pair[child, parent] = by_pair.get((child, parent), 0) + 1

    edges: list[GEdge] = []
    for child, parent, on, cols in raw:
        ambiguous = by_pair[child, parent] > 1
        suffix = f"_via_{cols}" if ambiguous else ""
        edges.append(
            GEdge(
                id=f"{child}_to_{parent}{suffix}",
                frm=child,
                to=parent,
                on=on,
                method_suffix=f"{parent}{suffix}",
                derived=True,
            )
        )
        edges.append(
            GEdge(
                id=f"{parent}_to_{child}{suffix}",
                frm=parent,
                to=child,
                on=on,
                method_suffix=f"{child}{suffix}",
                derived=True,
            )
        )
    edges.sort(key=lambda e: e.id)
    return edges


def _resolve_steps(
    decl: ShapeDecl, edges: Sequence[GEdge]
) -> tuple[GStep, ...]:
    present = [decl.root]
    steps: list[GStep] = []
    for entry in decl.steps:
        if entry.edge is not None:
            cands = [e for e in edges if e.id == entry.edge]
        else:
            cands = [
                e
                for e in edges
                if e.to == entry.target and e.frm in present and e.to not in present
            ]
        if len(cands) != 1:
            raise ValueError(
                f"shape {decl.root}+{[s.target for s in decl.steps]}: "
                f"{len(cands)} edges reach {entry.target!r} from {present}; "
                f"disambiguate with '<table>:<kind>#<edge-id>'"
            )
        e = cands[0]
        if entry.kind not in ("inner", "left"):
            raise ValueError(f"join kind must be 'inner' or 'left', got {entry.kind!r}")
        steps.append(GStep(e.id, e.to, entry.kind))
        present.append(e.to)
    return tuple(steps)


def _shape_class_name(
    root: str, steps: Sequence[GStep], edges: Sequence[GEdge]
) -> str:
    by_id = {e.id: e for e in edges}
    parts = [f"Q{camel(root)}"]
    for s in steps:
        parts.append(camel(by_id[s.edge].method_suffix))
        parts.append("I" if s.kind == "inner" else "N")
    return "".join(parts)


# ---------------------------------------------------------------------------
# top level
# ---------------------------------------------------------------------------


def build_schema(url: str, cfg: Config) -> GSchema:
    engine = create_engine(url)
    metadata = MetaData()
    warnings: list[str] = []

    with engine.connect() as connection:
        metadata.reflect(bind=connection)
        front = AltsaFrontend(metadata, connection, options=[])
        front.normalize()

        names = sorted(metadata.tables)
        if cfg.include_tables:
            names = [n for n in names if n in cfg.include_tables]
        if cfg.exclude_tables:
            names = [n for n in names if n not in cfg.exclude_tables]

        # ---- enums (deduplicated by sqlacodegen, sorted here) --------------
        enum_defs: dict[str, GEnum] = {}
        for (tname, cname), cls_name in sorted(front.enum_classes.items()):
            if tname not in names:
                continue
            col = metadata.tables[tname].c[cname]
            coltype = col.type
            values = (
                list(coltype.enums)
                if isinstance(coltype, Enum)
                else list(front.enum_values.get(cls_name, []))
            )
            native = isinstance(coltype, Enum) and bool(coltype.native_enum)
            db_name = coltype.name if isinstance(coltype, Enum) else None
            if cls_name not in enum_defs:
                enum_defs[cls_name] = GEnum(
                    class_name=cls_name,
                    members=tuple(
                        GEnumMember(enum_member_name(v), v) for v in values
                    ),
                    db_name=db_name if native else None,
                    native=native,
                )

        # ---- tables --------------------------------------------------------
        tables: list[GTable] = []
        for tname in names:
            table = metadata.tables[tname]
            cols = tuple(
                _classify(
                    table, c, front.enum_classes.get((tname, c.name)), warnings
                )
                for c in table.columns
            )
            tables.append(GTable(tname, camel(tname), cols))

        # ---- edges ---------------------------------------------------------
        edges: list[GEdge] = _fk_edges(metadata, names) if cfg.auto_edges else []
        known = {e.id for e in edges}
        for d in cfg.edges:
            if d.id in known:
                continue
            edges.append(
                GEdge(
                    id=d.id,
                    frm=d.frm,
                    to=d.to,
                    on=d.on,
                    method_suffix=d.method or d.to,
                    derived=False,
                )
            )
        edges.sort(key=lambda e: e.id)

    # ---- shapes ------------------------------------------------------------
    shapes: dict[tuple[str, tuple[tuple[str, str], ...]], GShape] = {}

    def add(root: str, steps: tuple[GStep, ...], declared: bool) -> GShape:
        key = (root, tuple((s.edge, s.kind) for s in steps))
        existing = shapes.get(key)
        if existing is not None:
            return existing
        sh = GShape(root, steps, _shape_class_name(root, steps, edges), declared)
        shapes[key] = sh
        return sh

    for t in tables:
        add(t.name, (), False)
    for e in edges:
        if e.frm not in names or e.to not in names:
            continue
        for kind in ("inner", "left"):
            add(e.frm, (GStep(e.id, e.to, kind),), False)

    declared_decls = list(cfg.shapes) + [p.shape for p in cfg.projections]
    for decl in declared_decls:
        steps = _resolve_steps(decl, edges)
        for i in range(1, len(steps) + 1):
            add(decl.root, steps[:i], i == len(steps))

    ordered_shapes = tuple(
        sorted(shapes.values(), key=lambda s: (s.root, len(s.steps), s.class_name))
    )

    # ---- projections -------------------------------------------------------
    by_name = {t.name: t for t in tables}
    projections: list[GProjection] = []
    for p in cfg.projections:
        steps = _resolve_steps(p.shape, edges)
        shape = shapes[(p.shape.root, tuple((s.edge, s.kind) for s in steps))]
        nulls = shape.nullability()
        fields: list[GProjField] = []
        for c in p.columns:
            gcol = next(
                g for g in by_name[c.table].columns if g.name == c.column
            )
            py = gcol.py_type
            if nulls.get(c.table, False) or not gcol.not_null:
                py = f"{py} | None"
            fields.append(GProjField(c.field, c.table, c.column, py))
        projections.append(
            GProjection(p.name, p.method, shape.class_name, tuple(fields))
        )
    projections.sort(key=lambda p: p.row_class)

    return GSchema(
        dialect=sqlalchemy.make_url(url).get_backend_name(),
        enums=tuple(sorted(enum_defs.values(), key=lambda e: e.class_name)),
        tables=tuple(tables),
        edges=tuple(edges),
        shapes=ordered_shapes,
        projections=tuple(projections),
        warnings=tuple(warnings),
    )
