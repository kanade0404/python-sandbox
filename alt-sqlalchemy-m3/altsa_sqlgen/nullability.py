"""Two-pass nullability inference, ported from sqlx.

Source of the algorithm:
`/Users/kanade0404/work/sqlx/sqlx-postgres/src/connection/describe.rs`
(sqlx is dual-licensed MIT OR Apache-2.0; Copyright (C) SQLx Contributors,
portions Copyright (C) LaunchBadge, LLC).

  Pass 1 -- CATALOG. The extended-query protocol's RowDescription gives, per
    result column, the OID of the table it came from and its attribute number
    (`ftable`/`ftablecol`; sqlx calls these `relation_id` /
    `relation_attribute_no`). One batched query turns those into
    `NOT attnotnull`. A column that is an expression -- or that the server
    could not attribute to a base table -- has table OID 0 and yields
    UNKNOWN.

  Pass 2 -- EXPLAIN. `EXPLAIN (VERBOSE, FORMAT JSON) EXECUTE <stmt>(NULL,...)`
    under `plan_cache_mode = force_generic_plan`. Columns produced on the
    null-extended side of an outer join get upgraded to nullable.

  COMBINE. EXPLAIN may only upgrade to nullable, never downgrade
    (`*nullable = patch.or(*nullable)` in sqlx). UNKNOWN then degrades to
    nullable, which is the safe direction: a `T | None` field that is never
    actually None is an inconvenience, a `T` field that is None is a bug.

------------------------------------------------------------------- DEVIATION
`visit_plan` here is NOT a transliteration of sqlx's. sqlx marks a node's
outputs nullable when

    join_type == "Full" || parent_relation == "Inner"

and recurses only through Left/Right nodes. That rule is correct for a plan
whose join direction matches the SQL, which is what sqlx's own tests cover
(`tests/postgres/postgres.rs::test_describe_outer_join_nullable` writes
`right join` explicitly, and the planner normalises it to a Left plan node).
It is INVERTED when the planner emits a genuine `"Join Type": "Right"`, which
it does when it flips a `LEFT JOIN` to put the smaller side on the hash's
build input. For a Right join node PostgreSQL preserves the INNER input and
null-extends the OUTER one -- exactly the opposite of what the rule assumes.

Observed on PostgreSQL 16 for corpus case `left_join_nested_right`:

    SELECT a.id, b.id, c.id
    FROM users a LEFT JOIN (orders b JOIN payments c ON c.order_id = b.id)
                        ON b.user_id = a.id

plans as `Hash Join / Join Type: Right` whose "Inner" child is `a` and whose
"Outer" child is the `b JOIN c` subtree. sqlx's rule marks `a.id` nullable (a
harmless false positive) and leaves `b.id`/`c.id` NOT NULL -- but a user with
no orders really does yield `(a.id, NULL, NULL)`, verified against live data.
That is an UNSOUND answer, so this port fixes it:

  * "Full"  -> both children are null-extended
  * "Left"  -> the child with Parent Relationship "Inner" is null-extended
  * "Right" -> the child with Parent Relationship "Outer" is null-extended
  * nullability is INHERITED down the whole subtree, not just applied to the
    direct child, so an intervening Sort/Hash/Materialize cannot lose it.

The second change (inheriting into the whole subtree) also makes the pass
independent of which node happens to carry the output name, which is what
lets `left_join_nested_right` resolve: the `b JOIN c` subtree's outputs
include `b.id` and `c.id`, and those match the top-level Output textually.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeGuard, cast

# `None` is sqlx's `Option<bool>` UNKNOWN: no opinion, degrade to nullable.
Nullable = bool | None

# Parent Relationship values that mean "this child feeds rows into its parent".
# InitPlan/SubPlan children are separate scalar computations; nullability still
# propagates into them (they can contain their own outer joins), but they never
# make their parent's rows nullable.
_ROW_CHILD: frozenset[str] = frozenset({"Outer", "Inner", "Member", "Subquery"})

JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
PlanNode = Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ColumnSource:
    """Where the server says a result column came from.

    `table_oid == 0` means "not a plain base-table column" -- an expression, a
    VALUES row, sometimes a set operation. sqlx leaves those as UNKNOWN and so
    do we.
    """

    table_oid: int
    attnum: int

    @property
    def is_table_column(self) -> bool:
        return self.table_oid != 0 and self.attnum > 0


# --------------------------------------------------------------------------
# 1. pass 2 -- the EXPLAIN plan walker (pure; unit-testable without a server)
# --------------------------------------------------------------------------


def nullables_from_explain(explain: object, outputs: Sequence[str]) -> list[Nullable]:
    """Walk an `EXPLAIN (VERBOSE, FORMAT JSON)` result.

    `explain` is the parsed JSON -- a list whose first element is normally
    `{"Plan": {...}}`. Anything else (a `"Utility Statement"` string, an
    object with no `"Plan"`) yields all-UNKNOWN, matching sqlx's `Explain::Other`
    fallback: we simply learn nothing and the catalog pass stands alone.

    Returns one entry per top-level output column: `True` (upgraded to
    nullable) or `None` (no opinion). It NEVER returns `False` -- this pass
    can only ever add nullability.
    """
    marks: list[Nullable] = [None] * len(outputs)
    plan = _top_plan(explain)
    if plan is None:
        return marks

    # Index by text so repeated names all get marked. Plan Output strings are
    # alias-qualified ("u.email"), so within one query level they are unique
    # per source column; a collision across levels can only over-approximate.
    positions: dict[str, list[int]] = {}
    for i, name in enumerate(outputs):
        positions.setdefault(name, []).append(i)

    def mark(node_outputs: Iterable[str]) -> None:
        for text in node_outputs:
            for i in positions.get(text, ()):
                marks[i] = True

    def visit(node: PlanNode, inherited: bool) -> None:
        if inherited:
            mark(_outputs(node))

        join_type = _str(node.get("Join Type"))
        children = _plans(node)

        # A Full join null-extends BOTH inputs, so the node's own outputs go
        # too (sqlx does the same) and every row-child inherits.
        if join_type == "Full":
            mark(_outputs(node))

        for child in children:
            rel = _str(child.get("Parent Relationship"))
            nulled = inherited
            if rel in _ROW_CHILD:
                if join_type == "Full":
                    nulled = True
                elif join_type == "Left" and rel == "Inner":
                    nulled = True
                elif join_type == "Right" and rel == "Outer":
                    nulled = True
            visit(child, nulled)

    visit(plan, False)
    return marks


def nullables_from_explain_sqlx_verbatim(
    explain: object, outputs: Sequence[str]
) -> list[Nullable]:
    """sqlx's `visit_plan`, transliterated exactly. Kept as a CONTROL.

    This is not used to generate anything. It exists so the corpus can score
    the upstream rule side by side with the corrected one and show, rather
    than assert, that the deviation documented at the top of this module is
    load-bearing. Corpus case `own/left_join_nested_right` is where they part
    company.

        if plan.join_type == Some("Full") || plan.parent_relation == Some("Inner") {
            mark all of this node's outputs nullable
        }
        if let Some("Left") | Some("Right") = plan.join_type {
            recurse into children
        }
    """
    marks: list[Nullable] = [None] * len(outputs)
    plan = _top_plan(explain)
    if plan is None:
        return marks

    positions: dict[str, list[int]] = {}
    for i, name in enumerate(outputs):
        positions.setdefault(name, []).append(i)

    def visit(node: PlanNode) -> None:
        if node.get("Output") is not None and (
            _str(node.get("Join Type")) == "Full"
            or _str(node.get("Parent Relationship")) == "Inner"
        ):
            for text in _outputs(node):
                for i in positions.get(text, ()):
                    marks[i] = True
        if _str(node.get("Join Type")) in ("Left", "Right"):
            for child in _plans(node):
                visit(child)

    visit(plan)
    return marks


def top_level_outputs(explain: object) -> tuple[str, ...]:
    """The root plan node's `Output` list -- the positional key for matching."""
    plan = _top_plan(explain)
    return () if plan is None else tuple(_outputs(plan))


def _top_plan(explain: object) -> PlanNode | None:
    """Reach the root `Plan` node, tolerating everything EXPLAIN can return.

    `["Utility Statement"]`, an object with no `"Plan"`, extra keys we do not
    model (sqlx issues #1449, #2587, #2622) -- all of them mean "learn
    nothing", never "crash".
    """
    node: object = explain
    if isinstance(node, list):
        # Parsed JSON is genuinely dynamically typed. This `cast` is the one
        # place it re-enters the type system; every access past here is guarded
        # by an isinstance check.
        items = cast("list[object]", node)
        node = items[0] if items else None
    if not _is_node(node):
        return None
    inner = node.get("Plan")
    return inner if _is_node(inner) else None


def _outputs(node: PlanNode) -> list[str]:
    raw = node.get("Output")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, str)]


def _plans(node: PlanNode) -> list[PlanNode]:
    raw = node.get("Plans")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if _is_node(p)]


def _is_node(value: object) -> TypeGuard[PlanNode]:
    return isinstance(value, dict)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


# --------------------------------------------------------------------------
# 2. combining the two passes
# --------------------------------------------------------------------------


def combine(catalog: Sequence[Nullable], explain: Sequence[Nullable]) -> list[Nullable]:
    """`patch.or(base)` -- EXPLAIN wins only when it has an opinion.

    sqlx: `*nullable = patch.or(*nullable)`.
    """
    if len(catalog) != len(explain):
        raise ValueError(f"length mismatch: {len(catalog)} catalog vs {len(explain)} explain")
    return [p if p is not None else c for c, p in zip(catalog, explain, strict=True)]


def resolve(value: Nullable) -> bool:
    """UNKNOWN degrades to nullable. sqlx: `describe.nullable(i).unwrap_or(true)`."""
    return True if value is None else value


# --------------------------------------------------------------------------
# 3. how a final answer was reached -- reported by the corpus runner
# --------------------------------------------------------------------------

Provenance = Literal["catalog", "explain", "unknown", "override"]


def provenance(catalog: Nullable, explain: Nullable, override: object = None) -> Provenance:
    if override is not None:
        return "override"
    if explain is not None:
        return "explain"
    if catalog is not None:
        return "catalog"
    return "unknown"


# --------------------------------------------------------------------------
# 4. pass 1 -- the batched catalog query
# --------------------------------------------------------------------------


def catalog_query(sources: Sequence[ColumnSource]) -> str:
    """Build sqlx's batched `NOT attnotnull` lookup.

    One `UNION ALL` arm per result column, then a LEFT JOIN onto
    `pg_catalog.pg_attribute`, ordered by the original column index so the
    result lines up positionally. Columns with `table_oid = 0` deliberately
    fall through the LEFT JOIN and come back NULL = UNKNOWN.

    sqlx builds this with `UNION ALL` rather than `VALUES` for pgwire-proxy
    compatibility; kept as-is since it costs nothing.

    DEVIATION: sqlx *binds* the three integers per column, which is what forces
    its `columns.len() * 3 > 65535` warning. These integers come from libpq's
    own `ftable`/`ftablecol` and are `int` by construction, so inlining them is
    both injection-free and free of the parameter ceiling. `_assert_int` keeps
    that invariant honest rather than assumed.
    """
    if not sources:
        raise ValueError("no columns")
    arms = [
        f"( SELECT {_assert_int(i)}::int4 AS idx, "
        f"{_assert_int(src.table_oid)}::oid AS table_id, "
        f"{_assert_int(src.attnum)}::int2 AS col_idx )"
        for i, src in enumerate(sources)
    ]
    return (
        "SELECT NOT attnotnull FROM ( "
        + " UNION ALL ".join(arms)
        + " ) AS col LEFT JOIN pg_catalog.pg_attribute"
        "  ON table_id <> 0 AND attrelid = table_id AND attnum = col_idx"
        " ORDER BY idx"
    )


def _assert_int(value: int) -> int:
    if type(value) is not int:  # noqa: E721 -- bool is an int subclass; reject it too
        raise TypeError(f"expected a plain int for SQL inlining, got {value!r}")
    return value
