"""THE SEAM: two layers, two connections, one database.

This module is the honest part of M5. Layer A and Layer B do not share a
connection, and nothing here pretends otherwise.

    Layer A  generated_a.facade -> altsa_runtime.Conn -> SQLAlchemy Engine
                                                      -> psycopg (as a DBAPI)
    Layer B  generated_b.*      -> psycopg.Connection  (its own socket)

Both point at the same PostgreSQL, with the same credentials, over two separate
backend sessions.

What that costs
---------------
**Atomicity does not cross the seam.** A Layer B `create_order` transaction and
a Layer A read are two different PostgreSQL transactions on two different
backends. Concretely:

  * Layer B functions CAN run on a psycopg connection while Layer A holds its
    own -- that is exactly what `OrderService` does, and it works. What they
    cannot do is enrol in each other's transaction.
  * Uncommitted Layer B work is INVISIBLE to Layer A. Until
    `service.commands()` exits and commits, a Layer A read sees the old rows.
  * There is no way to write "insert the order (Layer B) and, in the same
    transaction, run the Layer A search that decides whether to keep it".
    Either the decision moves inside Layer B's transaction as more SQL, or the
    operation is split and made idempotent.
  * Two connections can deadlock against each other exactly as two application
    processes can. A Layer A `for_update()` read and a Layer B
    `lock_products_for_update` taking the same rows in different orders is a
    genuine deadlock, and PostgreSQL will break it by killing one of them.

What it buys
------------
Nothing, in this design. The split is an ARTEFACT of two milestones built
independently -- M2 chose SQLAlchemy Core as its execution backend because that
was the milestone's thesis (a typed facade over the incumbent), and M3 chose
raw psycopg because it needed `pgconn.describe_prepared`. It is not a
considered architecture.

How a real application would manage it
-------------------------------------
Three options, in increasing order of how much they'd cost to build:

1. **Two pools, no shared transactions** (what this is). Give Layer A a
   SQLAlchemy `QueuePool` sized for the read workload and Layer B a
   `psycopg_pool.ConnectionPool` sized for the write workload; make every
   Layer B unit of work a single `with conn.transaction():` block that is
   self-contained, and treat Layer A as a read replica that happens to be the
   primary. Reads are then eventually-consistent WITH RESPECT TO YOUR OWN
   WRITES within a request, which is a real hazard: the pattern "POST then
   immediately GET" can miss its own write only if the write has not committed,
   so the rule is *commit before you read back*. That rule is enforceable by
   review, not by types.

2. **Layer A on Layer B's connection.** `altsa_runtime._backend.RuntimeConn`
   builds its own `Engine` from a DSN. SQLAlchemy can be pointed at an existing
   DBAPI connection (`creator=lambda: existing`, or
   `Engine.connect()` on a pool wrapping it), so a single psycopg connection
   could serve both. That is the fix worth building, and it is small -- the
   friction log lists it as F6 -- but it is a change to M2's runtime, which is
   audited and read-only for M5.

3. **A shared transaction bridge.** A `UnitOfWork` object owning one psycopg
   connection, handing Layer A a `Conn` bound to it and Layer B the raw handle,
   with one `commit()`. Out of scope for M5 by the brief, and it is really just
   option 2 plus an API.

Isolation note
--------------
PostgreSQL's default is READ COMMITTED, so once a Layer B transaction commits,
the very next statement on Layer A's connection sees it -- even though Layer
A's own transaction is still open. Under REPEATABLE READ that would NOT hold:
Layer A would keep its snapshot and never see the write until it committed or
rolled back. `OrderService.refresh_read()` exists to make that explicit; it
ends Layer A's read transaction so the next read starts a new one.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType
from typing import final

import psycopg

from altsa_runtime import Conn

#: Layer B's connection type, spelled the way the generated modules spell it.
#: `psycopg.Connection` bare would resolve to `tuple[Any, ...]` via PEP 696.
CommandConn = psycopg.Connection[tuple[object, ...]]


def sqlalchemy_url(dsn: str) -> str:
    """libpq DSN -> SQLAlchemy URL.

    F3 in the friction log: Layer A wants `postgresql+psycopg://...` and Layer B
    wants the libpq form, so every entry point has to carry this translation.
    """
    if dsn.startswith("postgresql+"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://") :]
    if dsn.startswith("postgres://"):
        return "postgresql+psycopg://" + dsn[len("postgres://") :]
    raise ValueError(f"cannot turn {dsn!r} into a SQLAlchemy URL")


@final
class OrderService:
    """Owns one connection per layer and hands each out under its own name.

    Deliberately NOT called `Session` or `UnitOfWork`: it is not one. It is two
    connections in a bag, and the type of `commands()` is the only place the
    application is told where the transaction boundary really is.
    """

    __slots__ = ("_commands", "_dsn", "_queries")

    def __init__(self, dsn: str) -> None:
        self._dsn: str = dsn
        # Layer A. `Conn.__enter__` opens the SQLAlchemy connection.
        self._queries: Conn = Conn(dsn=sqlalchemy_url(dsn))
        # Layer B. autocommit=True so that a statement outside an explicit
        # `with conn.transaction()` is its own transaction, and the connection
        # is never left idle-in-transaction between units of work.
        self._commands: CommandConn = psycopg.connect(dsn, autocommit=True)

    def __enter__(self) -> OrderService:
        self._queries.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._queries.__exit__(exc_type, exc, tb)
        self._commands.close()

    # -- Layer A --------------------------------------------------------------

    @property
    def queries(self) -> Conn:
        """The Layer A connection. Reads only, by convention -- the facade's
        typed writers exist, but M5 puts every write on Layer B."""
        return self._queries

    def refresh_read(self) -> None:
        """End Layer A's read transaction so the next read starts a new one.

        A no-op under READ COMMITTED as far as visibility goes; it matters
        under REPEATABLE READ, and it stops the read connection sitting
        idle-in-transaction holding an xmin back.
        """
        self._queries.rollback()

    # -- Layer B --------------------------------------------------------------

    @property
    def raw_commands(self) -> CommandConn:
        """The Layer B connection for a SINGLE-statement command.

        The connection is in autocommit, so one call is one transaction.
        """
        return self._commands

    @contextmanager
    def commands(self) -> Generator[CommandConn]:
        """One Layer B transaction.

        Everything inside commits together or rolls back together. NOTHING
        Layer A does is inside it -- see this module's docstring.
        """
        with self._commands.transaction():
            yield self._commands

    # -- fixtures -------------------------------------------------------------

    def truncate_all(self) -> None:
        """Test-fixture reset. Bytes, not str, because psycopg's `execute` is
        typed to demand a `LiteralString` -- M3's surprise #2, inherited."""
        self._commands.execute(
            b"TRUNCATE payments, order_items, orders, users, products "
            b"RESTART IDENTITY CASCADE"
        )
        self.refresh_read()
