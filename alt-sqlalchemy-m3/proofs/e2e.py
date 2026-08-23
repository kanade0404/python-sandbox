"""H7 -- run every generated EC function against a live, seeded PostgreSQL 16.

    uv run python proofs/e2e.py --url <dsn>

Type-checking proves the generated code is internally consistent. This proves
the types are TRUE OF THE DATABASE: the `| None` fields really do come back
None, the non-optional ones really never do, and the write-side queries
(upsert, FOR UPDATE, :exec) actually work.

The load-bearing assertion is the LEFT JOIN one: `seed_user_without_orders`
exists precisely so that `list_orders_left_join_users` returns a row whose
order columns are None. If the EXPLAIN pass had failed to mark them, those
fields would be typed `UUID`/`Decimal` and this script would be handing None
to code that promised otherwise.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg

# Run as a script (`python proofs/e2e.py`), so the project root is not on the
# path yet. pyright gets there via `extraPaths` in the proofs configs.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generated.orders import (
    get_order_with_user,
    list_orders_left_join_users,
    revenue_by_user,
    transition_order_status,
)
from generated.overrides import order_stats_for_user, user_email_maybe
from generated.products import insert_payment, lock_products_for_update, upsert_product

SEED = b"""
TRUNCATE payments, order_items, orders, users, products RESTART IDENTITY CASCADE;

INSERT INTO users (id, email, status) VALUES
  ('11111111-1111-1111-1111-111111111111', 'has-orders@example.com', 'active'),
  ('22222222-2222-2222-2222-222222222222', 'no-orders@example.com',  'active');

-- Two orders for the same user: `payments.order_id` is UNIQUE, so the second
-- one exists purely to give the second insert_payment call somewhere to go.
INSERT INTO orders (id, user_id, status, version, total) VALUES
  ('33333333-3333-3333-3333-333333333333',
   '11111111-1111-1111-1111-111111111111', 'pending', 1, 42.50),
  ('44444444-4444-4444-4444-444444444444',
   '11111111-1111-1111-1111-111111111111', 'pending', 1, 1.00);
"""

USER_WITH = UUID("11111111-1111-1111-1111-111111111111")
USER_WITHOUT = UUID("22222222-2222-2222-2222-222222222222")
ORDER = UUID("33333333-3333-3333-3333-333333333333")
ORDER2 = UUID("44444444-4444-4444-4444-444444444444")

_checks = 0
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    status = "ok  " if ok else "FAIL"
    print(f"  [{status}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        _failures.append(label)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="e2e")
    ap.add_argument("--url", required=True)
    args = ap.parse_args(argv)

    with psycopg.connect(args.url, autocommit=True) as raw:
        conn: psycopg.Connection[tuple[object, ...]] = raw
        conn.execute(SEED)

        # -- :one, INNER JOIN, and the `?` override --------------------------
        print("get_order_with_user (:one, JOIN, \"email?\" override)")
        order = get_order_with_user(conn, order_id=ORDER)
        check("returns a row", order is not None)
        if order is not None:
            check("id round-trips as UUID", order.id == ORDER)
            check("numeric -> Decimal", order.total == Decimal("42.50"), str(order.total))
            check("enum -> str", order.status == "pending", order.status)
            check(
                "overridden email is present at runtime",
                order.email == "has-orders@example.com",
                repr(order.email),
            )
            check("nullable jsonb column is None", order.metadata is None)

        print("get_order_with_user with an id that does not exist")
        missing = get_order_with_user(
            conn, order_id=UUID("00000000-0000-0000-0000-000000000000")
        )
        check(":one returns None for no rows", missing is None)

        # -- THE inference claim --------------------------------------------
        print("list_orders_left_join_users (:many, LEFT JOIN)")
        rows = list_orders_left_join_users(conn, min_total=Decimal("0"))
        # 2 rows for the user with two orders, 1 null-extended row for the other.
        check("one row per (user, order)", len(rows) == 3, f"{len(rows)} rows")
        without = [r for r in rows if r.user_id == USER_WITHOUT]
        check("the order-less user appears", len(without) == 1)
        if without:
            r = without[0]
            check(
                "LEFT JOIN: order columns really are None",
                r.order_id is None and r.total is None and r.order_status is None,
                f"order_id={r.order_id!r} total={r.total!r}",
            )
            check(
                "preserved side is NOT None, as typed",
                r.email == "no-orders@example.com" and r.user_status == "active",
            )
        with_orders = [r for r in rows if r.user_id == USER_WITH]
        check(
            "the user WITH orders has non-None order columns",
            len(with_orders) == 2 and all(r.order_id is not None for r in with_orders),
        )

        # -- aggregates, and the safe default --------------------------------
        print("revenue_by_user (:many, aggregate)")
        revenue = revenue_by_user(conn)
        check("one row per user (GROUP BY email)", len(revenue) == 2)
        empty = [r for r in revenue if r.email == "no-orders@example.com"]
        check(
            "SUM over an empty group really is None",
            bool(empty) and empty[0].revenue is None,
        )
        check(
            "COUNT is never None at runtime (hence the safe false positive)",
            all(r.order_count is not None for r in revenue),
        )

        # -- overrides, both directions --------------------------------------
        print('order_stats_for_user ("order_count!" forces NOT NULL)')
        stats = order_stats_for_user(conn, user_id=USER_WITHOUT)
        check("returns a row even with no orders", stats is not None)
        if stats is not None:
            check(
                "the `!`-overridden count is really not None",
                stats.order_count == 0,
                repr(stats.order_count),
            )
            check("the un-overridden SUM is None", stats.total_spent is None)

        print('user_email_maybe ("email?" forces nullable)')
        emails = user_email_maybe(conn)
        check(
            "the `?`-overridden column is non-None in practice",
            len(emails) == 2 and all(e.email is not None for e in emails),
            "the override costs an is-not-None check the data never needs",
        )

        # -- writes -----------------------------------------------------------
        print("upsert_product (INSERT ... ON CONFLICT ... RETURNING)")
        first = upsert_product(
            conn, sku="SKU-1", name="Widget", price=Decimal("9.99"), tags=["a", "b"], stock=3
        )
        check("insert returns the row", first is not None)
        if first is not None:
            # No isinstance here: `first.id` is already typed `int`, and
            # pyright rejects the check as unnecessary -- which is the point.
            check("identity id was assigned", first.id > 0, str(first.id))
            check("text[] -> list", first.tags == ["a", "b"], repr(first.tags))
        second = upsert_product(
            conn, sku="SKU-1", name="Widget v2", price=Decimal("11.50"), tags=["c"], stock=4
        )
        check("conflict path returns the row", second is not None)
        if first is not None and second is not None:
            check("same row was updated", first.id == second.id)
            check("stock accumulated", second.stock == 7, str(second.stock))
            check("name was replaced", second.name == "Widget v2")

        print("lock_products_for_update (SELECT ... FOR UPDATE)")
        # FOR UPDATE needs a real transaction to hold a lock, so drop
        # autocommit for this one call.
        conn.autocommit = False
        locked = lock_products_for_update(conn, skus=["SKU-1"])
        check("returns the locked rows", len(locked) == 1, f"{len(locked)} rows")
        blocked = _lock_is_held(args.url)
        check("the row lock is actually held", blocked, "a second txn could not lock it")
        conn.rollback()
        conn.autocommit = True

        print("insert_payment (:exec)")
        affected = insert_payment(
            conn, order_id=ORDER, method="card", amount=Decimal("42.50"), card_last4="4242"
        )
        check(":exec returns the row count", affected == 1, str(affected))
        nullable_ok = insert_payment(
            conn,
            order_id=ORDER2,
            method="bank",
            amount=Decimal("1.00"),
            card_last4=None,
        )
        check("the `?` parameter accepts None", nullable_ok == 1)

        print("transition_order_status (UPDATE ... RETURNING with a version check)")
        moved = transition_order_status(
            conn, new_status="paid", order_id=ORDER, expected_version=1
        )
        check("the winning transition returns a row", moved is not None)
        if moved is not None:
            check("version was bumped", moved.version == 2, str(moved.version))
            check("status changed", moved.status == "paid")
        stale = transition_order_status(
            conn, new_status="shipped", order_id=ORDER, expected_version=1
        )
        check("the stale transition returns None", stale is None)

    print()
    print(f"{_checks} check(s), {len(_failures)} failure(s)")
    for f in _failures:
        print(f"  FAILED: {f}")
    print("PASS" if not _failures else "FAIL")
    return 1 if _failures else 0


def _lock_is_held(url: str) -> bool:
    """A second connection must not be able to take the same row lock."""
    with psycopg.connect(url, autocommit=True) as other:
        other.execute("SET lock_timeout = '400ms'")
        try:
            other.execute(
                "SELECT id FROM products WHERE sku = 'SKU-1' FOR UPDATE NOWAIT"
            ).fetchall()
        except psycopg.errors.LockNotAvailable:
            return True
        except psycopg.Error:
            return False
    return False


if __name__ == "__main__":
    sys.exit(main())
