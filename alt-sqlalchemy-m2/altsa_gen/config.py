"""joins.toml -- the generator's only hand-written input.

Grammar
-------
    [options]
    module          = "facade"      # generated module name
    auto_edges      = true          # derive both directions of every FK
    include_tables  = []            # empty = all
    exclude_tables  = []

    [[edge]]                        # OPTIONAL: an edge the FKs do not give you
    id   = "orders_to_users"
    from = "orders"
    to   = "users"
    on   = ["users.id = orders.user_id"]
    method = "users"                # optional; default = `to`

    [[shape]]                       # multi-edge shapes must be DECLARED
    tables = ["orders", "users:left", "order_items:left"]

    [[projection]]                  # a named dataclass row, bound to one shape
    name   = "OrderSummaryRow"
    method = "select_order_summary"
    shape  = ["orders", "users:left"]
    columns = [
      { field = "order_id",   expr = "orders.id" },
      { field = "user_email", expr = "users.email" },
    ]

Shape entries are `<table>[:<kind>][#<edge-id>]`; `kind` defaults to `inner`.
The `#edge-id` part is only needed when more than one declared edge reaches the
same target from the tables already in the shape.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, final


@final
@dataclass(frozen=True, slots=True)
class EdgeDecl:
    id: str
    frm: str
    to: str
    on: tuple[tuple[str, str, str, str], ...]
    method: str | None


@final
@dataclass(frozen=True, slots=True)
class ShapeEntry:
    target: str
    kind: str
    edge: str | None


@final
@dataclass(frozen=True, slots=True)
class ShapeDecl:
    root: str
    steps: tuple[ShapeEntry, ...]


@final
@dataclass(frozen=True, slots=True)
class ProjFieldDecl:
    field: str
    table: str
    column: str


@final
@dataclass(frozen=True, slots=True)
class ProjectionDecl:
    name: str
    method: str
    shape: ShapeDecl
    columns: tuple[ProjFieldDecl, ...]


@final
@dataclass(frozen=True, slots=True)
class Config:
    module: str = "facade"
    auto_edges: bool = True
    include_tables: tuple[str, ...] = ()
    exclude_tables: tuple[str, ...] = ()
    edges: tuple[EdgeDecl, ...] = field(default=())
    shapes: tuple[ShapeDecl, ...] = field(default=())
    projections: tuple[ProjectionDecl, ...] = field(default=())


def _parse_on(items: list[str]) -> tuple[tuple[str, str, str, str], ...]:
    out: list[tuple[str, str, str, str]] = []
    for raw in items:
        lhs, _, rhs = raw.partition("=")
        if not rhs:
            raise ValueError(f"bad ON clause {raw!r}; want 'a.col = b.col'")
        lt, _, lc = lhs.strip().partition(".")
        rt, _, rc = rhs.strip().partition(".")
        if not (lt and lc and rt and rc):
            raise ValueError(f"bad ON clause {raw!r}; want 'a.col = b.col'")
        out.append((lt, lc, rt, rc))
    return tuple(out)


def _parse_shape(tables: list[str]) -> ShapeDecl:
    if not tables:
        raise ValueError("shape.tables must not be empty")
    root = tables[0]
    if ":" in root or "#" in root:
        raise ValueError(f"shape root {root!r} must be a bare table name")
    steps: list[ShapeEntry] = []
    for raw in tables[1:]:
        body, _, edge = raw.partition("#")
        target, _, kind = body.partition(":")
        steps.append(ShapeEntry(target.strip(), (kind or "inner").strip(), edge or None))
    return ShapeDecl(root, tuple(steps))


def load_config(path: Path) -> Config:
    data: dict[str, Any] = tomllib.loads(path.read_text())
    opts: dict[str, Any] = data.get("options", {})

    edges: list[EdgeDecl] = []
    for e in data.get("edge", []):
        edges.append(
            EdgeDecl(
                id=str(e.get("id") or f"{e['from']}_to_{e['to']}"),
                frm=str(e["from"]),
                to=str(e["to"]),
                on=_parse_on(list(e["on"])),
                method=(str(e["method"]) if "method" in e else None),
            )
        )

    shapes = [_parse_shape(list(s["tables"])) for s in data.get("shape", [])]

    projections: list[ProjectionDecl] = []
    for p in data.get("projection", []):
        cols: list[ProjFieldDecl] = []
        for c in p["columns"]:
            tbl, _, col = str(c["expr"]).partition(".")
            cols.append(ProjFieldDecl(str(c["field"]), tbl, col))
        projections.append(
            ProjectionDecl(
                name=str(p["name"]),
                method=str(p["method"]),
                shape=_parse_shape(list(p["shape"])),
                columns=tuple(cols),
            )
        )

    return Config(
        module=str(opts.get("module", "facade")),
        auto_edges=bool(opts.get("auto_edges", True)),
        include_tables=tuple(sorted(opts.get("include_tables", []))),
        exclude_tables=tuple(sorted(opts.get("exclude_tables", []))),
        edges=tuple(edges),
        shapes=tuple(shapes),
        projections=tuple(projections),
    )
