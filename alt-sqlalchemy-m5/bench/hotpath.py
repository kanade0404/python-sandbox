"""E2.1 + E2.2 -- what each layer costs per query, over the same SQL.

    uv run python -m bench.hotpath --url <dsn> [--iterations 2000]

ONE statement is measured five ways:

    SELECT id, sku, name, price, stock FROM products WHERE sku = $1

  a) Layer A   `from_products().where(...).select(...).fetch(conn)` -- the
     facade rebuilt per call, hitting its shape memo and SQLAlchemy's compiled
     cache.
  b) Core      a PREBUILT SQLAlchemy Core `Select` with a `bindparam`,
     executed on its own engine connection. The floor for anything that goes
     through SQLAlchemy.
  c) psycopg   `cur.execute(sql, params); cur.fetchall()` on a separate psycopg
     connection. The floor, full stop.
  d) Layer B   `get_product_by_sku(conn, sku=...)` -- generated: one cursor,
     one execute, one fetchone, one dataclass built from five `cast`s.
  e) hand      the same SQL executed by hand on the SAME psycopg connection as
     (d), returning the raw tuple. The difference (d) - (e) is exactly what the
     generated wrapper costs.

Everything shares one process and one machine, and every variant does the same
round trip to the same local PostgreSQL, so the network/planner cost is common
to all five and the DIFFERENCES are what mean anything.

Honest caveats, restated in the output:
  * macOS laptop, PostgreSQL 16 in Docker on localhost. Absolute numbers are
    meaningless off this machine.
  * (a) additionally rebuilds each row as a tuple and runs a per-column decoder
    (uuid/enum/numeric), which (b) does not; part of (a) - (b) is that work,
    not overhead.
  * (b) and (c) return SQLAlchemy `Row`s and raw tuples respectively; (a) and
    (d) return decoded values and a frozen dataclass. Compare shapes, not just
    numbers.
  * N is 2000 by default with a 200-iteration warmup, single-threaded, no
    isolation from the rest of the laptop. Medians, not means, and no
    confidence intervals are claimed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import final

import psycopg
import sqlalchemy
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    bindparam,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.engine.interfaces import CacheStats
from sqlalchemy.sql import Executable

from altsa_runtime import Conn
from app.seam import sqlalchemy_url
from generated_a.facade import PRODUCTS, from_products
from generated_b.products import get_product_by_sku

SQL = "SELECT id, sku, name, price, stock FROM products WHERE sku = %(sku)s"
SKU = "BENCH-1"

SEED = b"""
INSERT INTO products (sku, name, price, tags, stock)
VALUES ('BENCH-1', 'Bench widget', 9.99, ARRAY['bench'], 1000)
ON CONFLICT (sku) DO UPDATE SET stock = 1000
"""


@final
@dataclass(frozen=True, slots=True)
class Timing:
    label: str
    note: str
    samples: list[float]  # microseconds per call

    @property
    def median(self) -> float:
        return statistics.median(self.samples)

    @property
    def p10(self) -> float:
        return statistics.quantiles(self.samples, n=10)[0]

    @property
    def p90(self) -> float:
        return statistics.quantiles(self.samples, n=10)[8]


def measure_interleaved(
    variants: list[tuple[str, str, Callable[[], object]]], n: int, warmup: int
) -> list[Timing]:
    """Time every variant INSIDE one loop, rotating which goes first.

    Running each variant's 2000 iterations back to back makes the result a
    function of when it ran -- the laptop's clock speed, the page cache and the
    server's plan cache all drift over the run, and the first variant pays for
    warming things the others then reuse. That is exactly what the first draft
    of this bench showed: the generated Layer B function came out *faster* than
    the hand-written psycopg call it wraps, which cannot be true.

    Interleaving with a rotating start index spreads any drift evenly across
    the five and makes the medians comparable.
    """
    for _ in range(warmup):
        for _label, _note, fn in variants:
            fn()
    samples: list[list[float]] = [[] for _ in variants]
    k = len(variants)
    for i in range(n):
        for j in range(k):
            idx = (i + j) % k
            fn = variants[idx][2]
            t0 = time.perf_counter_ns()
            fn()
            samples[idx].append((time.perf_counter_ns() - t0) / 1000.0)
    return [
        Timing(label, note, samples[i])
        for i, (label, note, _fn) in enumerate(variants)
    ]


def core_table() -> Table:
    """A Core table built HERE, not borrowed from `altsa_runtime._backend`.

    (b) is meant to be "what you would write without any of this", so it uses
    its own MetaData and its own connection.
    """
    md = MetaData()
    return Table(
        "products",
        md,
        Column("id", Integer, primary_key=True),
        Column("sku", String),
        Column("name", String),
        Column("price", Numeric(10, 2, asdecimal=True)),
        Column("stock", Integer),
    )


def probe_compile_cache(
    layer_a: Conn, core_conn: Connection, stmt: Executable
) -> list[str]:
    """E2.1's other half: prove SQLAlchemy's COMPILED CACHE hits on the hot path.

    `Connection.execute` records the outcome on the execution context as a
    `CacheStats` member; the class-level `after_cursor_execute` event is the
    documented place to read it without reaching into private state. The
    listener is removed again before the timing loops so it costs nothing
    there.
    """
    seen: list[CacheStats] = []

    def _after(
        conn: object, cursor: object, statement: object,
        parameters: object, context: object, executemany: object,
    ) -> None:
        stats = getattr(context, "cache_hit", None)
        if isinstance(stats, CacheStats):
            seen.append(stats)

    event.listen(Engine, "after_cursor_execute", _after)
    lines: list[str] = []
    try:
        def run_a() -> None:
            (
                from_products()
                .where(PRODUCTS.sku.eq(SKU))
                .select(
                    PRODUCTS.id, PRODUCTS.sku, PRODUCTS.name,
                    PRODUCTS.price, PRODUCTS.stock,
                )
                .fetch(layer_a)
            )

        seen.clear()
        run_a()
        first = seen[-1] if seen else None
        run_a()
        second = seen[-1] if seen else None
        run_a()
        third = seen[-1] if seen else None
        lines.append(
            f"  Layer A   1st execution: {first.name if first else '?'}, "
            f"2nd: {second.name if second else '?'}, "
            f"3rd: {third.name if third else '?'}"
        )
        lines.append(
            f"  Layer A   compile-cache HIT on the hot path: "
            f"{'YES' if second is CacheStats.CACHE_HIT and third is CacheStats.CACHE_HIT else 'NO'}"
        )

        seen.clear()
        core_conn.execute(stmt, {"sku": SKU}).all()
        core_conn.execute(stmt, {"sku": SKU}).all()
        core_second = seen[-1] if seen else None
        lines.append(
            f"  Core      2nd execution of the prebuilt statement: "
            f"{core_second.name if core_second else '?'}"
        )
    finally:
        event.remove(Engine, "after_cursor_execute", _after)
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bench.hotpath")
    ap.add_argument("--url", required=True)
    ap.add_argument("--iterations", type=int, default=2000)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    n: int = args.iterations
    warmup: int = args.warmup

    # -- seed -----------------------------------------------------------------
    with psycopg.connect(args.url, autocommit=True) as setup:
        setup.execute(SEED)

    # -- (a) Layer A ----------------------------------------------------------
    layer_a = Conn(dsn=sqlalchemy_url(args.url))
    # -- (b) SQLAlchemy Core, its own engine ----------------------------------
    engine = create_engine(sqlalchemy_url(args.url))
    tab = core_table()
    stmt = (
        select(tab.c.id, tab.c.sku, tab.c.name, tab.c.price, tab.c.stock)
        .where(tab.c.sku == bindparam("sku"))
    )
    # -- (c) raw psycopg, its own connection ----------------------------------
    raw: psycopg.Connection[tuple[object, ...]] = psycopg.connect(
        args.url, autocommit=True
    )
    # -- (d)/(e) Layer B and its hand-written twin, ONE shared connection -----
    layer_b: psycopg.Connection[tuple[object, ...]] = psycopg.connect(
        args.url, autocommit=True
    )

    with layer_a, engine.connect() as core_conn, raw, layer_b:
        cache_lines = probe_compile_cache(layer_a, core_conn, stmt)
        hits0, misses0 = layer_a.statement_cache_stats()

        def a_facade() -> object:
            return (
                from_products()
                .where(PRODUCTS.sku.eq(SKU))
                .select(
                    PRODUCTS.id, PRODUCTS.sku, PRODUCTS.name,
                    PRODUCTS.price, PRODUCTS.stock,
                )
                .fetch(layer_a)
            )

        def b_core() -> object:
            return core_conn.execute(stmt, {"sku": SKU}).all()

        def c_psycopg() -> object:
            with raw.cursor() as cur:
                cur.execute(SQL, {"sku": SKU})
                return cur.fetchall()

        def d_layer_b() -> object:
            return get_product_by_sku(layer_b, sku=SKU)

        def e_hand() -> object:
            with layer_b.cursor() as cur:
                cur.execute(SQL, {"sku": SKU})
                return cur.fetchone()

        timings = measure_interleaved(
            [
                ("a) Layer A facade",
                 "generated facade -> altsa_runtime -> SQLAlchemy Core "
                 "(shape memo + compile cache)",
                 a_facade),
                ("b) SQLAlchemy Core",
                 "prebuilt Select + bindparam, executed directly",
                 b_core),
                ("c) raw psycopg",
                 "cur.execute + fetchall on a separate connection",
                 c_psycopg),
                ("d) Layer B generated",
                 "get_product_by_sku(): cursor + execute + fetchone + dataclass",
                 d_layer_b),
                ("e) hand-written psycopg",
                 "same SQL, same connection as (d), raw tuple back",
                 e_hand),
            ],
            n,
            warmup,
        )

        hits1, misses1 = layer_a.statement_cache_stats()

    calls_a = n + warmup
    print("environment")
    print(f"  python {'.'.join(str(v) for v in __import__('sys').version_info[:3])}, "
          f"sqlalchemy {sqlalchemy.__version__}, psycopg {psycopg.__version__}")
    print(f"  macOS laptop, PostgreSQL 16 in Docker on localhost. "
          f"n={n} timed iterations after {warmup} warmup, single process.")
    print("  RELATIVE numbers only.")
    print()
    print("compile-cache evidence")
    for line in cache_lines:
        print(line)
    print()
    print("facade shape-memo evidence (altsa_runtime's own memo, not SQLAlchemy's)")
    print(f"  over {calls_a} Layer A calls: +{hits1 - hits0} hits, "
          f"+{misses1 - misses0} misses")
    print(f"  memo hit rate on the hot path: "
          f"{(hits1 - hits0) / max(1, (hits1 - hits0) + (misses1 - misses0)) * 100:.2f}%")
    print()
    print(f"{'variant':<26} {'p10 us':>9} {'median us':>11} {'p90 us':>9}")
    print("-" * 60)
    for t in timings:
        print(f"{t.label:<26} {t.p10:>9.1f} {t.median:>11.1f} {t.p90:>9.1f}")
    print()
    by_label = {t.label: t for t in timings}
    a = by_label["a) Layer A facade"].median
    b = by_label["b) SQLAlchemy Core"].median
    c = by_label["c) raw psycopg"].median
    d = by_label["d) Layer B generated"].median
    e = by_label["e) hand-written psycopg"].median
    print("deltas (medians)")
    print(f"  Layer A over Core          : {a - b:+.1f} us  ({a / b:.2f}x)")
    print(f"  Core over raw psycopg      : {b - c:+.1f} us  ({b / c:.2f}x)")
    print(f"  Layer A over raw psycopg   : {a - c:+.1f} us  ({a / c:.2f}x)")
    print(f"  Layer B over hand-written  : {d - e:+.1f} us  ({d / e:.2f}x)")

    if args.json is not None:
        args.json.write_text(json.dumps(
            {
                "iterations": n,
                "warmup": warmup,
                "sqlalchemy": sqlalchemy.__version__,
                "psycopg": psycopg.__version__,
                "memo_hits": hits1 - hits0,
                "memo_misses": misses1 - misses0,
                "cache_lines": cache_lines,
                "timings": {
                    t.label: {
                        "note": t.note,
                        "p10_us": t.p10,
                        "median_us": t.median,
                        "p90_us": t.p90,
                    }
                    for t in timings
                },
            },
            indent=2,
        ) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
