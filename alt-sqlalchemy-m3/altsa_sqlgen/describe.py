"""Talk to PostgreSQL: describe a statement, then infer nullability.

MECHANISM. We use libpq's *describe without execute*, reached through
psycopg 3's `Connection.pgconn` escape hatch:

    pgconn.prepare(name, sql)          -> Parse   (server plans, does not run)
    pgconn.describe_prepared(name)     -> Describe(statement)

The `PGresult` that comes back carries everything the algorithm needs and is
the exact analogue of what sqlx reads off the wire:

    d.nfields / d.fname(i)     result column count and name
    d.ftype(i)                 column type OID
    d.ftable(i)                OID of the base table          (sqlx relation_id)
    d.ftablecol(i)             attribute number, 1-based      (sqlx attnum)
    d.nparams / d.param_type(i)  inferred parameter types

Why not the psycopg-level API: `Cursor.description` only exists *after*
`execute()`, and psycopg's own `prepare_threshold` machinery decides on its own
when to Parse. `pgconn.describe_prepared` is the only path that gives
`ftable`/`ftablecol` with a hard guarantee that the statement never ran. That
guarantee is the point -- generation must never execute a user's DML.

The one statement we DO run is `EXPLAIN (VERBOSE, FORMAT JSON) EXECUTE ...`.
Plain EXPLAIN plans without executing, for SELECT and for INSERT/UPDATE/DELETE
alike; only EXPLAIN ANALYZE executes, and it is never used here. Verified
empirically: after EXPLAINing an `INSERT ... ON CONFLICT ... RETURNING` and an
`INSERT INTO payments`, both tables still had 0 rows.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import psycopg
from psycopg import pq

from .annotations import ColumnName, NullabilityOverride, QueryBlock, parse_column_name
from .errors import GenerationError
from .nullability import (
    ColumnSource,
    Nullable,
    Provenance,
    catalog_query,
    combine,
    nullables_from_explain,
    provenance,
    resolve,
    top_level_outputs,
)
from .typemap import PyType, TypeInfo, python_type


class ExplainWalker(Protocol):
    """Pass 2's plan-walking strategy. See `nullability.py` for the two."""

    def __call__(self, explain: object, outputs: Sequence[str], /) -> list[Nullable]: ...


@dataclass(frozen=True, slots=True)
class ResultColumn:
    """One described result column, with its nullability fully resolved."""

    field: str
    """Python field name -- the alias with any `!`/`?` marker stripped."""
    sql_name: str
    """The raw name the server returned, marker included."""
    type_oid: int
    py: PyType
    nullable: bool
    catalog: Nullable
    explain: Nullable
    override: NullabilityOverride | None
    source: ColumnSource
    how: Provenance


@dataclass(frozen=True, slots=True)
class DescribedParam:
    name: str
    index: int
    type_oid: int
    py: PyType
    nullable: bool


@dataclass(frozen=True, slots=True)
class Described:
    block: QueryBlock
    columns: tuple[ResultColumn, ...]
    params: tuple[DescribedParam, ...]
    plan: object
    """The raw EXPLAIN JSON, kept for the evidence log."""


class Describer:
    """Wraps one connection that has been put into describe-friendly mode."""

    def __init__(
        self,
        conn: psycopg.Connection[tuple[object, ...]],
        *,
        explain_walker: ExplainWalker = nullables_from_explain,
    ) -> None:
        self._conn = conn
        self._types: dict[int, TypeInfo] = {}
        self._counter = 0
        # Swappable only so the corpus can score sqlx's verbatim rule as a
        # control -- generation always uses the default.
        self._walk = explain_walker
        # sqlx's `force_generic_plan_for_describe`: without this the planner may
        # specialise on the NULL arguments we bind for EXPLAIN and produce a
        # plan that does not reflect the real query shape (sqlx PR #3541).
        conn.execute("SET plan_cache_mode = force_generic_plan")

    # -- public ------------------------------------------------------------

    def describe(self, block: QueryBlock) -> Described:
        self._counter += 1
        stmt = f"altsa_stmt_{self._counter}"
        try:
            return self._describe(stmt, block)
        finally:
            # Named statements live for the whole session; a corpus run
            # describes hundreds of them against the same connection, and two
            # Describers over one connection would otherwise collide on the
            # counter. Freeing it here keeps the session stateless.
            self._deallocate(stmt)

    def _describe(self, stmt: str, block: QueryBlock) -> Described:
        res = self._prepare(stmt, block)

        nfields = res.nfields
        sources = [
            ColumnSource(table_oid=res.ftable(i), attnum=res.ftablecol(i))
            for i in range(nfields)
        ]
        raw_names = [self._decode(res.fname(i), i, block) for i in range(nfields)]
        oids = [res.ftype(i) for i in range(nfields)]
        param_oids = [res.param_type(i) for i in range(res.nparams)]

        if len(param_oids) != len(block.params):
            raise GenerationError(
                f"the server inferred {len(param_oids)} parameter(s) but the block "
                f"declares {len(block.params)} -- did a ${{var}} land inside a string "
                f"literal?",
                source=block.source,
                query=block.name,
            )

        catalog = self._catalog_pass(sources) if nfields else []
        explain, plan = self._explain_pass(stmt, block, nfields)
        merged = combine(catalog, explain)

        # -- overrides ----------------------------------------------------
        parsed: list[ColumnName] = []
        for raw in raw_names:
            try:
                parsed.append(parse_column_name(raw))
            except GenerationError as exc:
                raise GenerationError(
                    exc.raw_message, source=block.source, query=block.name
                ) from None

        by_field = {c.field: i for i, c in enumerate(parsed)}
        forced: list[NullabilityOverride | None] = [c.override for c in parsed]
        for col, mode in block.overrides:
            idx = by_field.get(col)
            if idx is None:
                raise GenerationError(
                    f"OVERRIDE names column {col!r}, which this query does not "
                    f"return. Result columns are: "
                    f"{', '.join(repr(c.field) for c in parsed) or '(none)'}",
                    source=block.source,
                    query=block.name,
                )
            if forced[idx] is not None:
                raise GenerationError(
                    f"column {col!r} is overridden twice -- once by the alias marker "
                    f"and once by an OVERRIDE directive",
                    source=block.source,
                    query=block.name,
                )
            forced[idx] = mode

        self._load_types(set(oids) | set(param_oids))

        columns: list[ResultColumn] = []
        for i in range(nfields):
            ov = forced[i]
            field = parsed[i].field
            if not field.isidentifier() or field.startswith("__"):
                raise GenerationError(
                    f"result column {raw_names[i]!r} is not usable as a Python field "
                    f"name -- give it an alias",
                    source=block.source,
                    query=block.name,
                )
            nullable = resolve(merged[i]) if ov is None else (ov == "nullable")
            py = self._py(oids[i], block)
            columns.append(
                ResultColumn(
                    field=field,
                    sql_name=raw_names[i],
                    type_oid=oids[i],
                    py=py.optional() if nullable else py,
                    nullable=nullable,
                    catalog=catalog[i],
                    explain=explain[i],
                    override=ov,
                    source=sources[i],
                    how=provenance(catalog[i], explain[i], ov),
                )
            )

        dup = {c.field for c in columns if sum(1 for d in columns if d.field == c.field) > 1}
        if dup:
            raise GenerationError(
                f"duplicate result column name(s) {', '.join(sorted(dup))} -- "
                f"disambiguate with an alias",
                source=block.source,
                query=block.name,
            )

        params = tuple(
            DescribedParam(
                name=p.name,
                index=p.index,
                type_oid=param_oids[p.index - 1],
                py=(
                    self._py(param_oids[p.index - 1], block).optional()
                    if p.nullable
                    else self._py(param_oids[p.index - 1], block)
                ),
                nullable=p.nullable,
            )
            for p in block.params
        )

        if block.kind in ("one", "many") and not columns:
            raise GenerationError(
                f"`:{block.kind}` needs result columns but the statement returns none "
                f"-- use `:exec`, or add a RETURNING clause",
                source=block.source,
                query=block.name,
            )
        if block.kind == "exec" and columns:
            raise GenerationError(
                f"`:exec` must not return columns, but this statement returns "
                f"{len(columns)} -- use `:one` or `:many`",
                source=block.source,
                query=block.name,
            )

        return Described(block=block, columns=tuple(columns), params=params, plan=plan)

    # -- passes ------------------------------------------------------------

    def _prepare(self, stmt: str, block: QueryBlock) -> pq.abc.PGresult:
        sql = block.render_native()
        res = self._conn.pgconn.prepare(stmt.encode(), sql.encode())
        if res.status != pq.ExecStatus.COMMAND_OK:
            raise GenerationError(
                f"PREPARE failed: {res.get_error_message()}",
                source=block.source,
                query=block.name,
            )
        described = self._conn.pgconn.describe_prepared(stmt.encode())
        if described.status != pq.ExecStatus.COMMAND_OK:
            raise GenerationError(
                f"DESCRIBE failed: {described.get_error_message()}",
                source=block.source,
                query=block.name,
            )
        return described

    def _deallocate(self, stmt: str) -> None:
        # psycopg types `execute` to demand a `LiteralString`, which is a real
        # injection guard and worth keeping. Every dynamic statement in this
        # module is therefore passed as BYTES -- the deliberate, greppable way
        # of saying "not a literal, and I built it myself". `stmt` is
        # `altsa_stmt_<int>`, the catalog query inlines only libpq-supplied
        # ints, and the EXPLAIN text is that same statement name.
        try:
            self._conn.execute(f"DEALLOCATE {stmt}".encode())
        except psycopg.Error:
            # Nothing was prepared (PREPARE itself failed) -- nothing to free.
            self._conn.rollback()

    def _catalog_pass(self, sources: list[ColumnSource]) -> list[Nullable]:
        with self._conn.cursor() as cur:
            cur.execute(catalog_query(sources).encode())
            rows = cur.fetchall()
        return [r[0] if isinstance(r[0], bool) else None for r in rows]

    def _explain_pass(
        self, stmt: str, block: QueryBlock, nfields: int
    ) -> tuple[list[Nullable], object]:
        if nfields == 0:
            return [], None
        args = ", ".join(["NULL"] * len(block.params))
        sql = f"EXPLAIN (VERBOSE, FORMAT JSON) EXECUTE {stmt}" + (f"({args})" if args else "")
        try:
            with self._conn.cursor() as cur:
                cur.execute(sql.encode())
                row = cur.fetchone()
        except psycopg.Error as exc:
            # sqlx tolerates EXPLAIN failures (issue #1248): the catalog pass
            # still stands, and UNKNOWN degrades to nullable, so the answer
            # stays sound -- just less precise.
            self._conn.rollback()
            return [None] * nfields, {"altsa_explain_error": str(exc)}
        plan: object = row[0] if row is not None else None
        if isinstance(plan, (str, bytes)):
            plan = json.loads(plan)
        outputs = top_level_outputs(plan)
        if len(outputs) != nfields:
            # The plan's top Output does not line up with the result columns
            # (utility statements, some ModifyTable shapes). Learn nothing --
            # which is safe, because UNKNOWN degrades to nullable.
            return [None] * nfields, plan
        return self._walk(plan, outputs), plan

    # -- type registry -----------------------------------------------------

    def _load_types(self, oids: set[int]) -> None:
        want = {o for o in oids if o and o not in self._types}
        while want:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT oid::int4, typname, typtype::text, typelem::int4, "
                    "       coalesce(typbasetype, 0)::int4 "
                    "FROM pg_catalog.pg_type WHERE oid = ANY(%s)",
                    (sorted(want),),
                )
                found = cur.fetchall()
            follow: set[int] = set()
            for oid, name, typtype, elem, base in found:
                assert isinstance(oid, int) and isinstance(name, str)
                assert isinstance(typtype, str) and isinstance(elem, int) and isinstance(base, int)
                self._types[oid] = TypeInfo(
                    oid=oid, name=name, typtype=typtype, element_oid=elem, base_oid=base
                )
                follow.update(o for o in (elem, base) if o and o not in self._types)
            want = follow

    def _py(self, oid: int, block: QueryBlock) -> PyType:
        info = self._types.get(oid)
        if info is None:
            self._load_types({oid})
            info = self._types.get(oid)
        if info is None:
            raise GenerationError(
                f"unknown type OID {oid}", source=block.source, query=block.name
            )
        try:
            return python_type(info, self._types)
        except GenerationError as exc:
            raise GenerationError(
                exc.raw_message, source=block.source, query=block.name
            ) from None

    @staticmethod
    def _decode(raw: bytes | None, i: int, block: QueryBlock) -> str:
        if raw is None:
            raise GenerationError(
                f"result column {i} has no name -- add an alias",
                source=block.source,
                query=block.name,
            )
        return raw.decode()
