"""Probe: does Mapped[Decimal] actually preserve decimal money on SQLite?

Answer: no. SQLAlchemy converts to float on the way in (pysqlite has no Decimal
adapter), SQLite stores storage-class `real`, and the read path converts the
float back to Decimal -- so the type annotation says Decimal end to end while
the durable value is IEEE754.
"""

from __future__ import annotations

import decimal
import pathlib

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from models_sqlite import Products

DB = pathlib.Path(__file__).resolve().parent.parent / "trial.db"


def main() -> None:
    engine = create_engine(f"sqlite:///{DB}")
    with Session(engine) as s:
        s.add(
            Products(
                sku="PROBE-DECIMAL",
                name="probe",
                price=decimal.Decimal("0.145"),
                stock=0,
            )
        )
        s.commit()
        p = s.scalars(
            select(Products).where(Products.sku == "PROBE-DECIMAL")
        ).one()
        print("python value read back :", repr(p.price))
        raw = s.execute(
            text(
                "select typeof(price), price, cast(price as text) "
                "from products where sku='PROBE-DECIMAL'"
            )
        ).one()
        print("sqlite storage class   :", raw[0])
        print("sqlite raw / as text   :", raw[1], "/", raw[2])
        total = s.execute(select(func.sum(Products.price))).scalar_one()
        print("SUM(price) type        :", type(total).__name__, total)
        s.delete(p)
        s.commit()


if __name__ == "__main__":
    main()
