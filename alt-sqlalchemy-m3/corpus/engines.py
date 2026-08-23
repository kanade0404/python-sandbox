"""The engine interface the corpus scores against.

Deliberately narrow, because the point of the corpus is that M4's static
analyser can be dropped in beside M3's live-database inference and scored on
identical cases. An engine is anything that, given a connection to a database
where the case's schema has been applied, can name the result columns of a
query and say whether each one is nullable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import psycopg

from altsa_sqlgen.describe import Describer
from altsa_sqlgen.nullability import nullables_from_explain_sqlx_verbatim

from .cases import Case


@dataclass(frozen=True, slots=True)
class InferredColumn:
    name: str
    nullable: bool
    how: str
    """Free-text provenance -- shown in the report to explain a verdict."""


class Engine(Protocol):
    name: str

    def analyze(
        self, conn: psycopg.Connection[tuple[object, ...]], case: Case
    ) -> list[InferredColumn]: ...


class Phase1Engine:
    """altsa_sqlgen: catalog `attnotnull`, patched by EXPLAIN. See M3."""

    name = "altsa_sqlgen-phase1"

    def analyze(
        self, conn: psycopg.Connection[tuple[object, ...]], case: Case
    ) -> list[InferredColumn]:
        described = Describer(conn).describe(case.block())
        return [
            InferredColumn(name=c.field, nullable=c.nullable, how=c.how)
            for c in described.columns
        ]


class AlwaysNullableEngine:
    """The trivial sound engine. Present as the runner's own control.

    It must score zero UNSOUND on every case; if it ever does not, the runner
    or a fixture is wrong, not the engine.
    """

    name = "always-nullable-control"

    def analyze(
        self, conn: psycopg.Connection[tuple[object, ...]], case: Case
    ) -> list[InferredColumn]:
        described = Describer(conn).describe(case.block())
        return [
            InferredColumn(name=c.field, nullable=True, how="control") for c in described.columns
        ]


class SqlxVerbatimEngine:
    """Everything Phase 1 does, but with sqlx's plan walker transliterated.

    A control, not a candidate. Scoring it beside `Phase1Engine` turns the
    deviation documented in `altsa_sqlgen/nullability.py` from a claim into a
    measurement: whatever this engine reports UNSOUND and Phase 1 does not is
    exactly what the fix bought.
    """

    name = "sqlx-verbatim-control"

    def analyze(
        self, conn: psycopg.Connection[tuple[object, ...]], case: Case
    ) -> list[InferredColumn]:
        described = Describer(conn, explain_walker=nullables_from_explain_sqlx_verbatim).describe(
            case.block()
        )
        return [
            InferredColumn(name=c.field, nullable=c.nullable, how=c.how)
            for c in described.columns
        ]


ENGINES: dict[str, Engine] = {
    e.name: e for e in (Phase1Engine(), AlwaysNullableEngine(), SqlxVerbatimEngine())
}
