"""The public connection facade. No SQLAlchemy object is ever handed out.

SCHEMA-INDEPENDENT. Every method takes private `ir` types or plain builtins and
returns plain builtins.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import final

from ._backend import RuntimeConn
from .expr import Assign, Pred
from .ir import Node, Plan


@final
class Conn:
    __slots__ = ("_impl",)

    def __init__(self, dsn: str, *, echo_to: list[str] | None = None) -> None:
        self._impl: RuntimeConn = RuntimeConn(dsn, echo_to=echo_to)

    def __enter__(self) -> Conn:
        self._impl.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._impl.close()

    # -- the runtime seam ----------------------------------------------------

    def run_plan(self, plan: Plan, outs: tuple[Node, ...]) -> list[tuple[object, ...]]:
        return self._impl.run(plan, outs)

    def render_plan(self, plan: Plan, outs: tuple[Node, ...]) -> str:
        return self._impl.render(plan, outs)

    # -- writers, called by GENERATED per-table helpers ----------------------

    def insert_values(self, table: str, values: Mapping[str, object]) -> int:
        return self._impl.insert_values(table, values)

    def update_rows(
        self, table: str, assigns: Sequence[Assign[str]], where: Pred | None
    ) -> int:
        return self._impl.update_rows(
            table,
            [a.assignment() for a in assigns],
            None if where is None else where.sql_ir(),
        )

    def delete_rows(self, table: str, where: Pred | None) -> int:
        return self._impl.delete_rows(
            table, None if where is None else where.sql_ir()
        )

    # -- layer B escape hatch ------------------------------------------------

    def run_raw(
        self, sql: str, params: Mapping[str, object]
    ) -> list[tuple[object, ...]]:
        """Execute verbatim SQL and return undecoded rows. Used by `Raw`."""
        return self._impl.run_raw(sql, params)

    def exec_raw(self, sql: str, params: Mapping[str, object] | None = None) -> int:
        """LAYER B: run verbatim DML and return its rowcount."""
        return self._impl.exec_raw(sql, params if params is not None else {})

    # -- transactions --------------------------------------------------------

    def commit(self) -> None:
        self._impl.commit()

    def rollback(self) -> None:
        self._impl.rollback()

    # -- fixtures ------------------------------------------------------------

    def create_all(self) -> None:
        self._impl.create_all()

    def execute_ddl(self, statement: str) -> None:
        """Raw DDL, for tests/fixtures only. Deliberately not part of the
        typed query surface."""
        self._impl.execute_ddl(statement)

    def statement_cache_stats(self) -> tuple[int, int]:
        """(hits, misses) of the shape-keyed statement memo."""
        return self._impl.cache_stats()
