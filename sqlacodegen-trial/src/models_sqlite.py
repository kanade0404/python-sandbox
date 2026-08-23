from typing import Optional
import decimal
import enum

from sqlalchemy import CHAR, CheckConstraint, DECIMAL, Enum, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class OrdersStatus(str, enum.Enum):
    PENDING = 'pending'
    PAID = 'paid'
    SHIPPED = 'shipped'
    CANCELLED = 'cancelled'


class PaymentsMethod(str, enum.Enum):
    CARD = 'card'
    BANK = 'bank'
    WALLET = 'wallet'


class UsersStatus(str, enum.Enum):
    ACTIVE = 'active'
    SUSPENDED = 'suspended'
    DELETED = 'deleted'


class Accounts(Base):
    __tablename__ = 'accounts'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'basic'"))
    label: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("datetime('now')"))


class Products(Base):
    __tablename__ = 'products'
    __table_args__ = (
        CheckConstraint('price >= 0'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    tags: Mapped[Optional[str]] = mapped_column(Text)

    order_items: Mapped[list['OrderItems']] = relationship('OrderItems', back_populates='product')


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("status IN ('active','suspended','deleted')"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[UsersStatus] = mapped_column(Enum(UsersStatus, values_callable=lambda cls: [member.value for member in cls]), nullable=False, server_default=text("'active'"))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("datetime('now')"))
    metadata_: Mapped[Optional[str]] = mapped_column('metadata', Text)

    orders: Mapped[list['Orders']] = relationship('Orders', back_populates='user')


class Orders(Base):
    __tablename__ = 'orders'
    __table_args__ = (
        CheckConstraint("status IN ('pending','paid','shipped','cancelled')"),
        Index('orders_status_idx', 'status'),
        Index('orders_user_created_idx', 'user_id', 'created_at')
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'), nullable=False)
    status: Mapped[OrdersStatus] = mapped_column(Enum(OrdersStatus, values_callable=lambda cls: [member.value for member in cls]), nullable=False, server_default=text("'pending'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    total: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default=text('0'))
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("datetime('now')"))

    user: Mapped['Users'] = relationship('Users', back_populates='orders')
    order_items: Mapped[list['OrderItems']] = relationship('OrderItems', back_populates='order')
    payments: Mapped['Payments'] = relationship('Payments', uselist=False, back_populates='order')


class StaffAccounts(Accounts):
    __tablename__ = 'staff_accounts'

    id: Mapped[int] = mapped_column(ForeignKey('accounts.id'), primary_key=True)
    employee_no: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    department: Mapped[str] = mapped_column(Text, nullable=False)


class OrderItems(Base):
    __tablename__ = 'order_items'
    __table_args__ = (
        CheckConstraint('quantity > 0'),
        Index('order_items_product_idx', 'product_id')
    )

    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey('products.id'), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[decimal.Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)

    order: Mapped['Orders'] = relationship('Orders', back_populates='order_items')
    product: Mapped['Products'] = relationship('Products', back_populates='order_items')


class Payments(Base):
    __tablename__ = 'payments'
    __table_args__ = (
        CheckConstraint("method IN ('card','bank','wallet')"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey('orders.id'), nullable=False, unique=True)
    method: Mapped[PaymentsMethod] = mapped_column(Enum(PaymentsMethod, values_callable=lambda cls: [member.value for member in cls]), nullable=False)
    amount: Mapped[decimal.Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    paid_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("datetime('now')"))
    card_last4: Mapped[Optional[str]] = mapped_column(CHAR(4))
    bank_account: Mapped[Optional[str]] = mapped_column(Text)
    wallet_provider: Mapped[Optional[str]] = mapped_column(Text)

    order: Mapped['Orders'] = relationship('Orders', back_populates='payments')
