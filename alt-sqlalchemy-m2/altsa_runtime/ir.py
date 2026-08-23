"""Private SQL IR shared by generated facades and the Core backend.

SCHEMA-INDEPENDENT. Nothing here mentions a table or column of any particular
database; the IR only knows *shapes*.

The IR is VALUE-FREE IN ITS SHAPE: every literal is wrapped in `ParamNode`, and
`shape()` renders a param as a bare `p`. Two queries differing only in bound
values therefore produce the same shape key and share one memoised Core
`Select`.

Changed from M1: `JoinStep` now carries the *edge id* rather than only the
target table. M1 keyed the ON-clause lookup by target table name, which is
ambiguous as soon as a schema has two different ways to reach the same table
(the EC schema has `order_items -> orders` and `payments -> orders`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final


@final
@dataclass(frozen=True, slots=True)
class ColRefNode:
    table: str
    name: str

    def shape(self) -> str:
        return f"c({self.table}.{self.name})"


@final
@dataclass(frozen=True, slots=True)
class ParamNode:
    value: object

    def shape(self) -> str:
        return "p"


@final
@dataclass(frozen=True, slots=True)
class BinOpNode:
    op: str
    left: Node
    right: Node

    def shape(self) -> str:
        return f"({self.left.shape()}{self.op}{self.right.shape()})"


@final
@dataclass(frozen=True, slots=True)
class BoolNode:
    op: str  # "AND" | "OR"
    parts: tuple[Node, ...]

    def shape(self) -> str:
        return f"{self.op}[{','.join(p.shape() for p in self.parts)}]"


@final
@dataclass(frozen=True, slots=True)
class UnaryNode:
    op: str  # "ISNULL" | "NOTNULL" | "NOT"
    operand: Node

    def shape(self) -> str:
        return f"{self.op}({self.operand.shape()})"


@final
@dataclass(frozen=True, slots=True)
class InNode:
    operand: Node
    values: tuple[object, ...]

    def shape(self) -> str:
        return f"IN({self.operand.shape()},{len(self.values)})"


@final
@dataclass(frozen=True, slots=True)
class FuncNode:
    """An aggregate / scalar SQL function call.

    `decode_as` names the IR node whose read-side decoder the result inherits
    (e.g. `sum(orders.total)` decodes as `orders.total` -> Decimal). `None`
    means "no conversion" (COUNT).
    """

    name: str
    args: tuple[Node, ...]
    star: bool = False

    def shape(self) -> str:
        inner = "*" if self.star else ",".join(a.shape() for a in self.args)
        return f"{self.name}({inner})"


Node = (
    ColRefNode | ParamNode | BinOpNode | BoolNode | UnaryNode | InNode | FuncNode
)


@final
@dataclass(frozen=True, slots=True)
class JoinStep:
    edge: str  # id of a registered edge
    target: str
    kind: str  # "inner" | "left"


@final
@dataclass(frozen=True, slots=True)
class OrderStep:
    node: Node
    desc: bool

    def shape(self) -> str:
        return f"{self.node.shape()}{'-' if self.desc else '+'}"


@final
@dataclass(frozen=True, slots=True)
class Plan:
    root: str
    joins: tuple[JoinStep, ...] = ()
    wheres: tuple[Node, ...] = ()
    groups: tuple[Node, ...] = ()
    orders: tuple[OrderStep, ...] = ()
    limit: int | None = None
    offset: int | None = None
    for_update: bool = False

    def with_join(self, edge: str, target: str, kind: str) -> Plan:
        return Plan(
            self.root,
            (*self.joins, JoinStep(edge, target, kind)),
            self.wheres,
            self.groups,
            self.orders,
            self.limit,
            self.offset,
            self.for_update,
        )

    def with_wheres(self, nodes: tuple[Node, ...]) -> Plan:
        return Plan(
            self.root,
            self.joins,
            (*self.wheres, *nodes),
            self.groups,
            self.orders,
            self.limit,
            self.offset,
            self.for_update,
        )

    def with_groups(self, nodes: tuple[Node, ...]) -> Plan:
        return Plan(
            self.root,
            self.joins,
            self.wheres,
            (*self.groups, *nodes),
            self.orders,
            self.limit,
            self.offset,
            self.for_update,
        )

    def with_orders(self, steps: tuple[OrderStep, ...]) -> Plan:
        return Plan(
            self.root,
            self.joins,
            self.wheres,
            self.groups,
            (*self.orders, *steps),
            self.limit,
            self.offset,
            self.for_update,
        )

    def with_limit(self, n: int) -> Plan:
        return Plan(
            self.root,
            self.joins,
            self.wheres,
            self.groups,
            self.orders,
            n,
            self.offset,
            self.for_update,
        )

    def with_offset(self, n: int) -> Plan:
        return Plan(
            self.root,
            self.joins,
            self.wheres,
            self.groups,
            self.orders,
            self.limit,
            n,
            self.for_update,
        )

    def with_for_update(self) -> Plan:
        return Plan(
            self.root,
            self.joins,
            self.wheres,
            self.groups,
            self.orders,
            self.limit,
            self.offset,
            True,
        )

    def shape(self) -> str:
        j = ",".join(f"{s.kind}:{s.edge}:{s.target}" for s in self.joins)
        w = ",".join(n.shape() for n in self.wheres)
        g = ",".join(n.shape() for n in self.groups)
        o = ",".join(s.shape() for s in self.orders)
        lim = "L" if self.limit is not None else ""
        off = "O" if self.offset is not None else ""
        fu = "FU" if self.for_update else ""
        return f"{self.root}|{j}|{w}|{g}|{o}|{lim}{off}{fu}"


@final
@dataclass(frozen=True, slots=True)
class Assignment:
    """One `column = value` pair of an UPDATE. Produced by `Col.set()`."""

    table: str
    column: str
    value: Node
