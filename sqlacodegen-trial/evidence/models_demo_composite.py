from typing import Any, Optional

from sqlalchemy import BigInteger, Identity, PrimaryKeyConstraint, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.sqltypes import NullType

class Base(DeclarativeBase):
    pass


class Warehouses(Base):
    __tablename__ = 'warehouses'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='warehouses_pkey'),
        {'schema': 'demo'}
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=1, increment=1, minvalue=1, maxvalue=9223372036854775807, cycle=False, cache=1), primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[Optional[Any]] = mapped_column(NullType)
