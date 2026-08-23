"""Private SQL IR shared by the typed facade and the Core backend.

Kept in its own module so `facade.py` (public, SQLAlchemy-free) and
`_runtime.py` (SQLAlchemy-only) can both depend on it without a cycle.

The IR is VALUE-FREE IN ITS SHAPE: every literal is wrapped in `_ParamNode`,
and `shape()` renders a param as a bare `p`. Two queries differing only in
bound values therefore produce the same shape key and share one memoised
Core `Select` -- the design doc's fig. 2 requirement, made structural rather
than a thing callers must remember.
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
    op: str  # "ISNULL" | "NOTNULL"
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


Node = ColRefNode | ParamNode | BinOpNode | BoolNode | UnaryNode | InNode


@final
@dataclass(frozen=True, slots=True)
class JoinStep:
    target: str
    kind: str  # "inner" | "left"


@final
@dataclass(frozen=True, slots=True)
class Plan:
    root: str
    joins: tuple[JoinStep, ...] = ()
    wheres: tuple[Node, ...] = ()

    def with_join(self, target: str, kind: str) -> Plan:
        return Plan(self.root, (*self.joins, JoinStep(target, kind)), self.wheres)

    def with_wheres(self, nodes: tuple[Node, ...]) -> Plan:
        return Plan(self.root, self.joins, (*self.wheres, *nodes))

    def shape(self) -> str:
        j = ",".join(f"{s.kind}:{s.target}" for s in self.joins)
        w = ",".join(n.shape() for n in self.wheres)
        return f"{self.root}|{j}|{w}"
