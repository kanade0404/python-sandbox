"""E1 -- the whole order service, both layers, against live PostgreSQL.

    uv run python -m app.scenario --url postgresql://postgres:postgres@localhost:55438/altsa

Every claim in this file is an assertion about the DATABASE, not about the
types: the types are checked by pyright, and this checks that they are TRUE.
The load-bearing ones are

  * the stock decrement really happened, and the failed order really rolled
    ALL of its decrements back (including the line that succeeded);
  * the optimistic transition detects a stale version;
  * the LEFT JOIN listing gives the order-less user four Nones;
  * the revenue aggregate equals a Decimal computed by hand here;
  * Layer A and Layer B agree about the same order; and
  * an uncommitted Layer B transaction is INVISIBLE to Layer A -- the seam,
    demonstrated rather than asserted in prose.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal

from app import commands, queries
from app.domain import OrderLine, OutOfStock, VersionConflict
from app.queries import OrderSearch
from app.seam import OrderService
from generated_a.facade import OrderStatus, UserStatus
from generated_b.orders import get_order_with_user

WIDGET = Decimal("9.99")
GADGET = Decimal("24.50")
GIZMO = Decimal("5.00")

_checks = 0
_failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global _checks
    _checks += 1
    print(f"  [{'ok  ' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        _failures.append(label)


def stock_of(service: OrderService, sku: str) -> int:
    """Read stock through Layer B's `get_product_by_sku` (one statement, own
    transaction). Deliberately NOT through Layer A: this is the value the
    command side is about to act on."""
    from generated_b.products import get_product_by_sku

    row = get_product_by_sku(service.raw_commands, sku=sku)
    if row is None:
        raise AssertionError(f"product {sku} vanished")
    return row.stock


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scenario")
    ap.add_argument("--url", required=True)
    args = ap.parse_args(argv)

    with OrderService(args.url) as service:
        service.truncate_all()

        # ------------------------------------------------------ registration
        print("register (Layer B, one statement per call)")
        buyer = commands.register(service.raw_commands, email="buyer@example.com")
        lurker = commands.register(
            service.raw_commands, email="lurker@example.com", status=UserStatus.ACTIVE
        )
        # No `isinstance(buyer.id, UUID)`: pyright rejects it as unnecessary,
        # because the generated row already types it `UUID`. What is worth
        # checking is that the SERVER filled it in.
        check("register returns a non-nil server-generated uuid", buyer.id.int != 0)
        check("status round-trips", buyer.status == "active", buyer.status)
        check("two distinct users", buyer.id != lurker.id)

        # ---------------------------------------------------------- catalogue
        print("upsert_products (Layer B, INSERT ... ON CONFLICT)")
        commands.upsert(
            service.raw_commands,
            sku="WIDGET", name="Widget", price=WIDGET, stock=10, tags=("tools",),
        )
        commands.upsert(
            service.raw_commands,
            sku="GADGET", name="Gadget", price=GADGET, stock=5, tags=("tools", "new"),
        )
        gizmo = commands.upsert(
            service.raw_commands, sku="GIZMO", name="Gizmo", price=GIZMO, stock=4
        )
        check("upsert assigned an identity id", gizmo.id > 0, str(gizmo.id))
        topped = commands.upsert(
            service.raw_commands, sku="GIZMO", name="Gizmo", price=GIZMO, stock=3
        )
        check("conflict path accumulates stock", topped.stock == 7, str(topped.stock))
        check("conflict path keeps the same row", topped.id == gizmo.id)

        # ------------------------------------------- create_order (the txn)
        print("create_order (Layer B transaction: FOR UPDATE + decrement + insert)")
        before = {sku: stock_of(service, sku) for sku in ("WIDGET", "GADGET", "GIZMO")}
        with service.commands() as conn:
            order1 = commands.create_order(
                conn,
                user_id=buyer.id,
                lines=[OrderLine("WIDGET", 2), OrderLine("GADGET", 1)],
            )
            # THE SEAM, demonstrated: Layer B has written but not committed;
            # Layer A is a different backend and cannot see it.
            mid = queries.search_orders(service.queries, OrderSearch())
            check(
                "Layer A cannot see Layer B's UNCOMMITTED order",
                len(mid) == 0,
                f"{len(mid)} row(s) visible mid-transaction",
            )
        service.refresh_read()
        expected1 = WIDGET * 2 + GADGET
        check("total is computed from the LOCKED prices", order1.total == expected1,
              f"{order1.total} vs {expected1}")
        check("server default status", order1.status == "pending", order1.status)
        check("server default version", order1.version == 1, str(order1.version))
        after = {sku: stock_of(service, sku) for sku in ("WIDGET", "GADGET", "GIZMO")}
        check("WIDGET stock decremented by 2", after["WIDGET"] == before["WIDGET"] - 2,
              f"{before['WIDGET']} -> {after['WIDGET']}")
        check("GADGET stock decremented by 1", after["GADGET"] == before["GADGET"] - 1,
              f"{before['GADGET']} -> {after['GADGET']}")
        check("untouched product is untouched", after["GIZMO"] == before["GIZMO"])
        check("Layer A sees the COMMITTED order",
              len(queries.search_orders(service.queries, OrderSearch())) == 1)

        # -------------------------------------------- the rollback assertion
        print("create_order that runs out of stock (partial work must vanish)")
        before2 = {sku: stock_of(service, sku) for sku in ("WIDGET", "GIZMO")}
        orders_before = len(queries.search_orders(service.queries, OrderSearch()))
        raised = False
        short: OutOfStock | None = None
        try:
            with service.commands() as conn:
                # WIDGET succeeds, GIZMO cannot -- and the WIDGET decrement,
                # the order header and the first line all have to go with it.
                commands.create_order(
                    conn,
                    user_id=buyer.id,
                    lines=[OrderLine("WIDGET", 1), OrderLine("GIZMO", 99)],
                )
        except OutOfStock as exc:
            raised = True
            short = exc
        check("OutOfStock was raised", raised)
        if short is not None:
            check("it names the offending sku", short.sku == "GIZMO", short.sku)
            check("it reports what was available", short.available == 7, str(short.available))
        after2 = {sku: stock_of(service, sku) for sku in ("WIDGET", "GIZMO")}
        check("the SUCCESSFUL line's decrement was rolled back too",
              after2["WIDGET"] == before2["WIDGET"], f"{before2['WIDGET']} -> {after2['WIDGET']}")
        check("no half-written order survived",
              len(queries.search_orders(service.queries, OrderSearch())) == orders_before)

        # ------------------------------------------------------ second order
        with service.commands() as conn:
            order2 = commands.create_order(
                conn, user_id=buyer.id, lines=[OrderLine("GIZMO", 3)]
            )
        service.refresh_read()
        expected2 = GIZMO * 3
        check("second order total", order2.total == expected2, str(order2.total))

        # ---------------------------------------------------------- payment
        print("record_payment (Layer B, :one with RETURNING)")
        paid = commands.pay(
            service.raw_commands, order_id=order1.id, method="card",
            amount=order1.total, card_last4="4242",
        )
        check("payment amount matches the order", paid.amount == expected1)
        check("payment is bound to the order", paid.order_id == order1.id)
        nullable = commands.pay(
            service.raw_commands, order_id=order2.id, method="bank", amount=order2.total
        )
        check("the nullable `card_last4` parameter accepts None", nullable.id > paid.id)

        # ------------------------------------------- optimistic transition
        print("transition (Layer B, optimistic version check)")
        moved = commands.transition(
            service.raw_commands, order_id=order1.id, expected_version=1,
            new_status=OrderStatus.PAID,
        )
        check("the winning transition bumps the version", moved.version == 2, str(moved.version))
        check("status changed", moved.status == "paid", moved.status)
        conflict = False
        try:
            commands.transition(
                service.raw_commands, order_id=order1.id, expected_version=1,
                new_status=OrderStatus.SHIPPED,
            )
        except VersionConflict:
            conflict = True
        check("the STALE transition is detected", conflict)
        still = get_order_with_user(service.raw_commands, order_id=order1.id)
        check("the losing transition changed nothing",
              still is not None and still.status == "paid" and still.version == 2)
        service.refresh_read()

        # ------------------------------------- Layer A: the dynamic search
        print("search_orders (Layer A, every subset of three criteria)")
        past = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        future = dt.datetime(2999, 1, 1, tzinfo=dt.timezone.utc)
        subsets: list[tuple[str, OrderSearch, int]] = [
            ("{}", OrderSearch(), 2),
            ("{status}", OrderSearch(status=OrderStatus.PAID), 1),
            ("{after}", OrderSearch(created_after=past), 2),
            ("{min_total}", OrderSearch(min_total=Decimal("20")), 1),
            ("{status, after}", OrderSearch(status=OrderStatus.PENDING, created_after=past), 1),
            ("{status, min_total}",
             OrderSearch(status=OrderStatus.PAID, min_total=Decimal("20")), 1),
            ("{after, min_total}",
             OrderSearch(created_after=future, min_total=Decimal("0")), 0),
            ("{status, after, min_total}",
             OrderSearch(status=OrderStatus.PAID, created_after=past,
                         min_total=Decimal("44.48")), 1),
        ]
        for label, criteria, expected in subsets:
            rows = queries.search_orders(service.queries, criteria)
            check(f"search {label} -> {expected} row(s)", len(rows) == expected,
                  f"got {len(rows)}")
        rows = queries.search_orders(service.queries, OrderSearch())
        check("search rows carry the joined email",
              all(r.user_email == "buyer@example.com" for r in rows))
        check("search rows carry the generated enum, not a string",
              all(type(r.status) is OrderStatus for r in rows),
              repr([r.status for r in rows]))

        hits_before, misses_before = service.queries.statement_cache_stats()
        for _ in range(3):
            queries.search_orders(service.queries, OrderSearch(status=OrderStatus.PAID))
        hits_after, misses_after = service.queries.statement_cache_stats()
        check("repeating a search shape is a facade memo HIT",
              hits_after - hits_before == 3 and misses_after == misses_before,
              f"+{hits_after - hits_before} hits, +{misses_after - misses_before} misses")

        # --------------------------------- Layer A: the LEFT JOIN listing
        print("list_users_with_orders (Layer A, LEFT JOIN -> None for the order-less)")
        listing = queries.list_users_with_orders(service.queries)
        check("one row per (user, order), plus the null-extended one",
              len(listing) == 3, f"{len(listing)} rows")
        empty = [r for r in listing if r.user_id == lurker.id]
        check("the order-less user appears exactly once", len(empty) == 1)
        if empty:
            r = empty[0]
            check("all four order fields are None",
                  r.order_id is None and r.status is None
                  and r.total is None and r.created_at is None,
                  f"order_id={r.order_id!r} total={r.total!r}")
            check("the preserved side is NOT None", r.email == "lurker@example.com")
        latest = queries.latest_order_per_user(service.queries)
        check("latest_order_per_user gives one row per user", len(latest) == 2)
        buyer_latest = [r for r in latest if r.user_id == buyer.id]
        check("the buyer's latest order is the second one",
              len(buyer_latest) == 1 and buyer_latest[0].order_id == order2.id)
        lurker_latest = [r for r in latest if r.user_id == lurker.id]
        check("the lurker's latest order is None",
              len(lurker_latest) == 1 and lurker_latest[0].order_id is None)

        # ----------------------------------------- Layer A: the aggregate
        print("revenue (Layer A, sum_ -> Decimal | None)")
        hand_computed = (WIDGET * 2 + GADGET) + (GIZMO * 3)
        total = queries.revenue(service.queries)
        check("revenue equals the hand-computed Decimal",
              total == hand_computed, f"{total} vs {hand_computed}")
        none_total = queries.revenue(
            service.queries, OrderSearch(min_total=Decimal("100000"))
        )
        check("SUM over an empty set really is None -- hence `Decimal | None`",
              none_total is None, repr(none_total))
        per_user = queries.revenue_by_user(service.queries)
        check("revenue_by_user groups to one row", len(per_user) == 1, str(len(per_user)))
        check("and its total matches",
              len(per_user) == 1 and per_user[0][1] == hand_computed)

        # ------------------------------------------- cross-layer agreement
        print("both layers, same order")
        b_side = get_order_with_user(service.raw_commands, order_id=order1.id)
        a_side = [
            r for r in queries.search_orders(service.queries, OrderSearch())
            if r.order_id == order1.id
        ]
        check("Layer B found it", b_side is not None)
        check("Layer A found it", len(a_side) == 1)
        if b_side is not None and len(a_side) == 1:
            check("same total", b_side.total == a_side[0].total)
            check("same email", b_side.email == a_side[0].user_email)
            check("same status, spelled differently by design",
                  b_side.status == a_side[0].status.value,
                  f"Layer B {b_side.status!r} (str) vs Layer A {a_side[0].status!r} (enum)")

    print()
    print(f"{_checks} check(s), {len(_failures)} failure(s)")
    for f in _failures:
        print(f"  FAILED: {f}")
    print("PASS" if not _failures else "FAIL")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
