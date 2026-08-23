"""Q2 probe: enumerate every place `Any` (or an Any-equivalent unchecked hole)
appears in the SQLAlchemy 2.0 + pyright-strict stack, using the sqlacodegen-
generated PostgreSQL models (``models_pg``).

This file is never executed -- it exists purely so `pyright` can statically
report the inferred type at each `reveal_type(...)` call. Run:

    uv run --project . pyright src/q_any_probe.py

Each numbered section corresponds to one item in the Q2 task list.
"""

from __future__ import annotations

import decimal
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, literal, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, aliased, with_polymorphic

from models_pg import Accounts, Products, StaffAccounts, Users

if TYPE_CHECKING:
    from typing import reveal_type


# --------------------------------------------------------------------------- 1
# row[0] / row.email for Row[Tuple[Users-ish scalars]] via
# session.execute(select(Users.email, Users.id)).all()
def probe_1_row_getitem_vs_attr(session: Session) -> None:
    stmt = select(Users.email, Users.id)
    rows = session.execute(stmt).all()
    row = rows[0]
    if TYPE_CHECKING:
        reveal_type(stmt)  # Q2.1 stmt
        reveal_type(rows)  # Q2.1 rows
        reveal_type(row)  # Q2.1 row
        reveal_type(row[0])  # Q2.1 row[0]  <-- Row.__getitem__
        reveal_type(row.email)  # Q2.1 row.email  <-- Row.__getattr__


# --------------------------------------------------------------------------- 2
# select() with 11+ scalar column arguments -> Select[Any]?
def probe_2_wide_select(session: Session) -> None:
    stmt = select(
        Users.id,  # 1
        Users.email,  # 2
        Users.status,  # 3
        Users.created_at,  # 4
        literal(0),  # 5
        literal(1),  # 6
        literal(2),  # 7
        literal(3),  # 8
        literal(4),  # 9
        literal(5),  # 10
        literal(6),  # 11
    )
    rows = session.execute(stmt).all()
    if TYPE_CHECKING:
        reveal_type(stmt)  # Q2.2 stmt (11 columns)
        reveal_type(rows)  # Q2.2 rows
        reveal_type(rows[0])  # Q2.2 rows[0]


# --------------------------------------------------------------------------- 3
# func.some_unknown_function(...) vs func.count(...)
def probe_3_func_typing() -> None:
    unknown = func.some_unknown_function(Users.id)
    counted = func.count(Users.id)
    if TYPE_CHECKING:
        reveal_type(unknown)  # Q2.3 func.some_unknown_function(...)
        reveal_type(counted)  # Q2.3 func.count(...)


# --------------------------------------------------------------------------- 4
# pg_insert(Products).excluded.price
def probe_4_excluded_column() -> None:
    ins = pg_insert(Products).values(sku="x", name="y", price=decimal.Decimal("1"))
    excluded = ins.excluded
    price = excluded.price
    if TYPE_CHECKING:
        reveal_type(excluded)  # Q2.4 excluded (the EXCLUDED pseudo-table)
        reveal_type(price)  # Q2.4 excluded.price


# --------------------------------------------------------------------------- 5
# session.execute(update(Products).values(...)) result type + .values(**kw)
def probe_5_update_values(session: Session) -> None:
    stmt_ok = update(Products).values(price=decimal.Decimal("9.99"), stock=1)
    stmt_bogus = update(Products).values(
        totally_bogus_column_name=123  # should this be a pyright error?
    )
    result = session.execute(stmt_ok)
    if TYPE_CHECKING:
        reveal_type(stmt_ok)  # Q2.5 update(...).values(real kwargs)
        reveal_type(stmt_bogus)  # Q2.5 update(...).values(bogus kwarg)
        reveal_type(result)  # Q2.5 session.execute(update(...))


# --------------------------------------------------------------------------- 6
# text("SELECT 1") execution result element types
def probe_6_text_result(session: Session) -> None:
    stmt = text("SELECT 1")
    result = session.execute(stmt)
    rows = result.all()
    row = rows[0]
    value = row[0]
    if TYPE_CHECKING:
        reveal_type(stmt)  # Q2.6 text(...)
        reveal_type(result)  # Q2.6 session.execute(text(...))
        reveal_type(rows)  # Q2.6 result.all()
        reveal_type(row)  # Q2.6 row
        reveal_type(value)  # Q2.6 row[0]


# --------------------------------------------------------------------------- 7
# bare Mapped[Optional[dict]] jsonb column access
def probe_7_jsonb_metadata(u: Users) -> None:
    md = u.metadata_
    if TYPE_CHECKING:
        reveal_type(md)  # Q2.7 u.metadata_
    if md is not None:
        val = md["k"]
        if TYPE_CHECKING:
            reveal_type(val)  # Q2.7 u.metadata_["k"] (after None-narrowing)


# --------------------------------------------------------------------------- 8
# aliased(Users) attribute access AND with_polymorphic AliasedClass __getattr__
def probe_8_aliased_and_polymorphic() -> None:
    u_alias = aliased(Users)
    alias_email = u_alias.email
    alias_typo = u_alias.emailllll  # deliberate typo

    wp = with_polymorphic(Accounts, [StaffAccounts])
    poly_dept = wp.StaffAccounts.department
    poly_typo = wp.StaffAccounts.not_a_real_attr  # deliberate typo

    if TYPE_CHECKING:
        reveal_type(u_alias)  # Q2.8 aliased(Users)
        reveal_type(alias_email)  # Q2.8 aliased(Users).email
        reveal_type(alias_typo)  # Q2.8 aliased(Users).emailllll (typo)
        reveal_type(wp)  # Q2.8 with_polymorphic(Accounts, [StaffAccounts])
        reveal_type(wp.StaffAccounts)  # Q2.8 wp.StaffAccounts
        reveal_type(poly_dept)  # Q2.8 wp.StaffAccounts.department
        reveal_type(poly_typo)  # Q2.8 wp.StaffAccounts.not_a_real_attr (typo)


# --------------------------------------------------------------------------- 9
# ORM constructor kwargs: Users(bogus_kwarg=1) -- checked or not?
def probe_9_constructor_kwargs() -> None:
    good = Users(email="a@example.com")
    bad = Users(bogus_kwarg=1)  # should this be a pyright error?
    if TYPE_CHECKING:
        reveal_type(good)  # Q2.9 Users(email=...)
        reveal_type(bad)  # Q2.9 Users(bogus_kwarg=1)


# --------------------------------------------------------------------------- 10
# Session.get(Users, some_id) -- control case
def probe_10_session_get(session: Session, some_id: uuid.UUID) -> None:
    got = session.get(Users, some_id)
    if TYPE_CHECKING:
        reveal_type(got)  # Q2.10 session.get(Users, some_id)
