"""Deliberate negative checks -- evidence for the known type holes.

Excluded from the main strict run; check it on its own with:

    uv run pyright -p pyrightconfig.negative.json

Each item is annotated with what pyright ACTUALLY reports (see
evidence/pyright_negative_checks.txt), not what one might hope.
"""

from __future__ import annotations

from sqlalchemy import Nullable, select, update
from sqlalchemy.orm import Session

from models_pg import Orders, Users


def checks(session: Session) -> None:
    # (a) NOT CAUGHT. ColumnOperators.__eq__ is typed `(other: Any)`, so
    #     comparing a text column to an int is invisible to the checker.
    _a = Users.email == 5

    # (b) NOT CAUGHT. Same reason: `in_` takes an untyped iterable.
    _b = Users.email.in_([1, 2, 3])

    # (c) CAUGHT (3 diagnostics). Unknown attribute on a mapped entity.
    row = session.execute(select(Users)).scalar_one()
    _c = row.nonexistent

    # (d) NOT CAUGHT. LEFT OUTER JOIN nullability: `o` is statically `Orders`
    #     but is None at runtime for users with no orders -> AttributeError.
    for _u, o in session.execute(
        select(Users, Orders).outerjoin(Orders, Orders.user_id == Users.id)
    ).tuples():
        _d = o.id

    # (e) CAUGHT. The identical access, once Nullable() is applied.
    for _u2, o2 in session.execute(
        select(Users, Nullable(Orders)).outerjoin(Orders, Orders.user_id == Users.id)
    ).tuples():
        _e = o2.id

    # (f) NOT CAUGHT. `.values()` takes **kwargs: Any -- a misspelled column
    #     name and a bogus enum value both survive type checking and only blow
    #     up at execution time.
    _f = update(Orders).values(status="not-a-real-status", verzion=99)

    # (g) NOT CAUGHT. Constructor kwargs on a DeclarativeBase model are not
    #     validated: wrong type, and a column that does not exist.
    _g = Orders(user_id="not-a-uuid", nonexistent_column=1)

    # (h) NOT CAUGHT. Arithmetic between a numeric and a text column.
    _h = Orders.total + Users.email
