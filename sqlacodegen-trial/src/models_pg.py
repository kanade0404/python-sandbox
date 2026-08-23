from typing import Optional
import datetime
import decimal
import enum
import uuid

from sqlalchemy import BigInteger, CHAR, CheckConstraint, DateTime, Enum, ForeignKeyConstraint, Identity, Index, Integer, Numeric, PrimaryKeyConstraint, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class OrderStatus(str, enum.Enum):
    PENDING = 'pending'
    PAID = 'paid'
    SHIPPED = 'shipped'
    CANCELLED = 'cancelled'


class UserStatus(str, enum.Enum):
    ACTIVE = 'active'
    SUSPENDED = 'suspended'
    DELETED = 'deleted'


class Accounts(Base):
    __tablename__ = 'accounts'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='accounts_pkey'),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=1, increment=1, minvalue=1, maxvalue=9223372036854775807, cycle=False, cache=1), primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'basic'::text"))
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))


class Products(Base):
    __tablename__ = 'products'
    __table_args__ = (
        CheckConstraint('price >= 0::numeric', name='products_price_nonneg'),
        PrimaryKeyConstraint('id', name='products_pkey'),
        UniqueConstraint('sku', name='products_sku_key'),
        Index('products_tags_gin', 'tags', postgresql_using='gin')
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=1, increment=1, minvalue=1, maxvalue=9223372036854775807, cycle=False, cache=1), primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    tags: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text()))

    order_items: Mapped[list['OrderItems']] = relationship('OrderItems', back_populates='product')


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='users_pkey'),
        UniqueConstraint('email', name='users_email_key'),
        Index('users_metadata_gin', 'metadata', postgresql_using='gin')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    email: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus, values_callable=lambda cls: [member.value for member in cls], name='user_status'), nullable=False, server_default=text("'active'::user_status"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    metadata_: Mapped[Optional[dict]] = mapped_column('metadata', JSONB)

    orders: Mapped[list['Orders']] = relationship('Orders', back_populates='user')


class Orders(Base):
    __tablename__ = 'orders'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='orders_user_id_fkey'),
        PrimaryKeyConstraint('id', name='orders_pkey'),
        Index('orders_status_idx', 'status'),
        Index('orders_user_created_idx', 'user_id', 'created_at')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus, values_callable=lambda cls: [member.value for member in cls], name='order_status'), nullable=False, server_default=text("'pending'::order_status"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    total: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text('0'))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    user: Mapped['Users'] = relationship('Users', back_populates='orders')
    order_items: Mapped[list['OrderItems']] = relationship('OrderItems', back_populates='order')
    payments: Mapped['Payments'] = relationship('Payments', uselist=False, back_populates='order')


class StaffAccounts(Accounts):
    __tablename__ = 'staff_accounts'
    __table_args__ = (
        ForeignKeyConstraint(['id'], ['accounts.id'], ondelete='CASCADE', name='staff_accounts_id_fkey'),
        PrimaryKeyConstraint('id', name='staff_accounts_pkey'),
        UniqueConstraint('employee_no', name='staff_accounts_employee_no_key')
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_no: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str] = mapped_column(Text, nullable=False)


class OrderItems(Base):
    __tablename__ = 'order_items'
    __table_args__ = (
        CheckConstraint('quantity > 0', name='order_items_qty_pos'),
        ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE', name='order_items_order_id_fkey'),
        ForeignKeyConstraint(['product_id'], ['products.id'], name='order_items_product_id_fkey'),
        PrimaryKeyConstraint('order_id', 'product_id', name='order_items_pkey'),
        Index('order_items_product_idx', 'product_id')
    )

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[decimal.Decimal] = mapped_column(Numeric(10, 2), nullable=False)

    order: Mapped['Orders'] = relationship('Orders', back_populates='order_items')
    product: Mapped['Products'] = relationship('Products', back_populates='order_items')


class Payments(Base):
    __tablename__ = 'payments'
    __table_args__ = (
        CheckConstraint("method = ANY (ARRAY['card'::text, 'bank'::text, 'wallet'::text])", name='payments_method_check'),
        ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE', name='payments_order_id_fkey'),
        PrimaryKeyConstraint('id', name='payments_pkey'),
        UniqueConstraint('order_id', name='payments_order_id_key')
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(start=1, increment=1, minvalue=1, maxvalue=9223372036854775807, cycle=False, cache=1), primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    card_last4: Mapped[Optional[str]] = mapped_column(CHAR(4))
    bank_account: Mapped[Optional[str]] = mapped_column(Text)
    wallet_provider: Mapped[Optional[str]] = mapped_column(Text)

    order: Mapped['Orders'] = relationship('Orders', back_populates='payments')
