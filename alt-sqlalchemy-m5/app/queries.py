"""The QUERY side -- Layer A (M2), the composable typed facade.

Everything here is a read whose SHAPE varies at runtime: a search whose WHERE
clause is assembled from whichever criteria the caller supplied, a listing whose
join is fixed but whose ordering is not, an aggregate. That is the half a .sql
file cannot do -- a Layer B query with three optional filters is either eight
.sql files or one file full of `(${status} IS NULL OR status = ${status})`.

Nothing here writes. Layer A's generated `insert_*`/`update_*`/`delete_*`
writers exist and type-check; M5 does not use them, because every write in this
service needs RETURNING or a conditional, and Layer A has neither.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import final
from uuid import UUID

from altsa_runtime import Conn
from generated_a.facade import (
    ORDERS,
    USERS,
    OrderSearchRow,
    OrderStatus,
    Pred,
    UserOrderRow,
    all_of,
    from_orders,
    from_users,
    sum_,
)

from .compat import enum_operand


@final
@dataclass(frozen=True, slots=True)
class OrderSearch:
    """Every field optional; ANY SUBSET is a valid search.

    Eight combinations of three criteria, one function, one SQL shape per
    combination -- and the statement memo in `altsa_runtime._backend` keys on
    exactly that shape, so the eight compile once each and then hit the cache.
    """

    status: OrderStatus | None = None
    created_after: dt.datetime | None = None
    min_total: Decimal | None = None

    def predicates(self) -> list[Pred]:
        preds: list[Pred] = []
        if self.status is not None:
            # `ORDERS.status.eq(self.status)` type-checks and then fails at
            # runtime with `operator does not exist: order_status = character
            # varying`. See app/compat.py -- friction F11.
            preds.append(ORDERS.status.eq(enum_operand(self.status)))
        if self.created_after is not None:
            preds.append(ORDERS.created_at.gte(self.created_after))
        if self.min_total is not None:
            preds.append(ORDERS.total.gte(self.min_total))
        return preds


def search_orders(
    conn: Conn, criteria: OrderSearch, *, limit: int = 50
) -> list[OrderSearchRow]:
    """Dynamic search over orders + their user.

    `all_of()` with no arguments is a no-op (jOOQ `noCondition` semantics), so
    an empty `OrderSearch` is "everything" without a special case.
    """
    return (
        from_orders()
        .join_users()
        .where(all_of(*criteria.predicates()))
        .order_by(ORDERS.created_at.desc(), ORDERS.id.asc())
        .limit(limit)
        .select_order_search()
        .fetch(conn)
    )


def list_users_with_orders(conn: Conn) -> list[UserOrderRow]:
    """LEFT JOIN listing: one row per (user, order), and one row of Nones for
    a user with no orders.

    `UserOrderRow.order_id`/`status`/`total`/`created_at` are `| None` because
    the generator computed the join's nullability from the shape -- nobody
    declared it, and there is no way to spell this shape and get the non-null
    variant.
    """
    return (
        from_users()
        .left_join_orders()
        .order_by(USERS.email.asc(), ORDERS.created_at.desc())
        .select_user_orders()
        .fetch(conn)
    )


@final
@dataclass(frozen=True, slots=True)
class LatestOrder:
    """One user and their most recent order, or `None` for all four order
    fields if they have never ordered."""

    user_id: UUID
    email: str
    order_id: UUID | None
    status: OrderStatus | None
    total: Decimal | None
    created_at: dt.datetime | None


def latest_order_per_user(conn: Conn) -> list[LatestOrder]:
    """"Users with their latest order" -- FOLDED IN PYTHON, not in SQL.

    This is friction F15 and it is the sharpest one on the query side. The SQL
    for this is `DISTINCT ON (u.id) ... ORDER BY u.id, o.created_at DESC`, or a
    window function, or a LATERAL subquery. Layer A has none of the three: no
    DISTINCT, no window functions, no subqueries. So the service fetches every
    (user, order) pair and reduces client-side -- correct, and O(orders)
    network instead of O(users).

    The escape hatch would be `Raw[LatestOrder]("SELECT DISTINCT ON ...")`,
    which type-checks its ROW but not its SQL, and matches the dataclass
    POSITIONALLY at runtime. Using it here would move the risk rather than
    remove it, so the fold stays in Python and the limitation stays visible.
    """
    best: dict[UUID, LatestOrder] = {}
    for row in list_users_with_orders(conn):
        current = best.get(row.user_id)
        candidate = LatestOrder(
            user_id=row.user_id,
            email=row.email,
            order_id=row.order_id,
            status=row.status,
            total=row.total,
            created_at=row.created_at,
        )
        if current is None:
            best[row.user_id] = candidate
            continue
        if row.created_at is None:
            continue
        if current.created_at is None or row.created_at > current.created_at:
            best[row.user_id] = candidate
    return sorted(best.values(), key=lambda r: r.email)


def revenue(conn: Conn, criteria: OrderSearch | None = None) -> Decimal | None:
    """Total revenue over the searched orders.

    The return type is `Decimal | None`, not `Decimal`, and that is the point:
    `SUM` over an empty set is NULL in SQL, `sum_` is typed
    `Expr[T] -> Expr[T | None]`, and the honesty survives all the way out to
    this signature. `coalesce(sum_(...), Decimal(0))` is available and would
    give `Decimal` -- but it would be the application ASSERTING a default, not
    the database reporting one, and the caller should get to make that choice.
    """
    preds = criteria.predicates() if criteria is not None else []
    row = (
        from_orders()
        .where(all_of(*preds))
        .select(sum_(ORDERS.total))
        .fetch_one(conn)
    )
    return None if row is None else row[0]


def revenue_by_user(conn: Conn) -> list[tuple[str, Decimal | None]]:
    """GROUP BY over users JOIN orders.

    No HAVING: Layer A has no `having()` clause (friction F15), so "users who
    spent more than X" has to be filtered after the fetch, and the filtering
    cannot be pushed to the server.
    """
    return (
        from_users()
        .join_orders()
        .group_by(USERS.email)
        .order_by(USERS.email.asc())
        .select(USERS.email, sum_(ORDERS.total))
        .fetch(conn)
    )
