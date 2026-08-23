"""Q1 repro: identity-map staleness on ORM-enabled upsert-with-RETURNING.

Reproduces, against the live ``trial.db`` (SQLite dialect, same
``on_conflict_do_update(...).returning(...)`` API shape as PostgreSQL), the
behavior documented at
https://docs.sqlalchemy.org/en/20/orm/queryguide/dml.html#orm-queryguide-upsert-returning :

  "This option indicates that User objects which are already present in the
  Session for rows that already exist should be refreshed with the data from
  the new row."

Concretely: within a single Session, once a Products row is present in the
identity map, an upsert-with-RETURNING targeting that same primary key will,
by default, hand back the *pre-upsert* in-memory attribute values -- even
though the UPDATE really happened and the row in the DB is correct -- unless
``execution_options={"populate_existing": True}`` is passed.

Run with:
    uv run --project . python src/q_upsert_repro.py
"""

from __future__ import annotations

import decimal
import pathlib

from sqlalchemy import create_engine, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from models_sqlite import Products

DB = pathlib.Path(__file__).resolve().parent.parent / "trial.db"


def banner(msg: str) -> None:
    print(f"\n########## {msg} ##########", flush=True)


def main() -> None:
    engine = create_engine(f"sqlite:///{DB}")

    with Session(engine) as s:
        banner("0) load SKU-3 into the identity map")
        product = s.scalars(select(Products).where(Products.sku == "SKU-3")).one()
        print(
            "-> in-memory BEFORE upsert:",
            "id=", product.id, "name=", product.name,
            "price=", product.price, "stock=", product.stock,
        )
        pk = product.id

        banner("1) upsert WITHOUT populate_existing")
        new_name_1 = "Doohickey MkIII (no populate_existing)"
        new_price_1 = decimal.Decimal("7.77")
        stmt1 = sqlite_insert(Products).values(
            sku="SKU-3", name=new_name_1, price=new_price_1, stock=1
        )
        stmt1 = stmt1.on_conflict_do_update(
            index_elements=[Products.sku],
            set_={
                "name": stmt1.excluded.name,
                "price": stmt1.excluded.price,
                "stock": Products.stock + stmt1.excluded.stock,
            },
        ).returning(Products)
        returned1 = s.scalars(stmt1).one()
        print(
            "-> RETURNING-object attrs (NO populate_existing):",
            "same object as before?", returned1 is product,
            "name=", returned1.name, "price=", returned1.price, "stock=", returned1.stock,
        )

        raw1 = s.connection().exec_driver_sql(
            "SELECT name, price, stock FROM products WHERE id = ?", (pk,)
        ).one()
        print("-> actual DB row after upsert #1 (raw SQL, bypasses ORM):", tuple(raw1))
        # NOTE: no commit() here on purpose -- see the rollback() at the very
        # end. Both upserts stay inside one uncommitted transaction so this
        # script leaves the shared trial.db byte-for-byte unchanged on exit
        # (another agent may be concurrently reading/writing it). The raw SQL
        # query above still observes the uncommitted UPDATE because it runs on
        # the *same* DBAPI connection/transaction as the ORM statement.

        banner("2) upsert WITH populate_existing")
        new_name_2 = "Doohickey MkIV (WITH populate_existing)"
        new_price_2 = decimal.Decimal("8.88")
        stmt2 = sqlite_insert(Products).values(
            sku="SKU-3", name=new_name_2, price=new_price_2, stock=1
        )
        stmt2 = stmt2.on_conflict_do_update(
            index_elements=[Products.sku],
            set_={
                "name": stmt2.excluded.name,
                "price": stmt2.excluded.price,
                "stock": Products.stock + stmt2.excluded.stock,
            },
        ).returning(Products)
        returned2 = s.scalars(
            stmt2, execution_options={"populate_existing": True}
        ).one()
        print(
            "-> RETURNING-object attrs (WITH populate_existing):",
            "same object as before?", returned2 is product,
            "name=", returned2.name, "price=", returned2.price, "stock=", returned2.stock,
        )
        print(
            "-> the original `product` reference itself, post populate_existing:",
            "name=", product.name, "price=", product.price, "stock=", product.stock,
        )

        raw2 = s.connection().exec_driver_sql(
            "SELECT name, price, stock FROM products WHERE id = ?", (pk,)
        ).one()
        print("-> actual DB row after upsert #2 (raw SQL, bypasses ORM):", tuple(raw2))
        s.rollback()

    banner("DONE")


if __name__ == "__main__":
    main()
