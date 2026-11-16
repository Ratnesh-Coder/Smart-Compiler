"""Abstract Syntax Tree node classes for the supported C subset.

Every node records its source position (``line``, ``column``, ``offset``, ``length``) of the
token that best identifies it (e.g. the identifier for a declarator or function, the operator
keyword for statements). Location fields are keyword-only so tests can build trees tersely.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Iterator, Optional


@dataclass(eq=False)
class Node:
    """Base class for all AST nodes."""

    line: int = field(default=0, kw_only=True)
    column: int = field(default=0, kw_only=True)
    offset: int = field(default=-1, kw_only=True)
    length: int = field(default=0, kw_only=True)

    def children(self) -> Iterator["Node"]:
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Node):
                yield value
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        yield item

    @property
    def node_name(self) -> str:
        return type(self).__name__


class Expression(Node):
    """Marker base class for expression nodes."""


class Statement(Node):
    """Marker base class for statement nodes."""


# ----------------------------------------------------------------------------- program
@dataclass(eq=False)
class ProgramNode(Node):
    declarations: list[Node] = field(default_factory=list)


# ----------------------------------------------------------------------------- declarations
@dataclass(eq=False)
class DeclaratorNode(Node):
    """One declared name inside a declaration: ``x``, ``y = 3`` or ``a[10]``."""

    name: str = ""
    is_array: bool = False
    array_size: Optional[Node] = None
    initializer: Optional[Node] = None
    size_omitted: bool = False


@dataclass(eq=False)
class DeclarationNode(Statement):
    """A variable declaration statement: ``int a, b = 2;``."""

    type_name: str = "int"
    declarators: list[DeclaratorNode] = field(default_factory=list)
    type_offset: int = -1
    type_length: int = 0


@dataclass(eq=False)
class InitializerListNode(Expression):
    elements: list[Node] = field(default_factory=list)


@dataclass(eq=False)
class ParameterNode(Node):
    type_name: str = "int"
    name: Optional[str] = None
    is_array: bool = False
    type_offset: int = -1
    type_length: int = 0


@dataclass(eq=False)
class FunctionNode(Node):
    """Function definition (``body`` set) or prototype (``body`` is ``None``).

    The location fields refer to the function *name* token.
    """

    return_type: str = "int"
    name: str = ""
    parameters: list[ParameterNode] = field(default_factory=list)
    body: Optional["CompoundNode"] = None
    type_offset: int = -1
    type_length: int = 0


# ----------------------------------------------------------------------------- statements
@dataclass(eq=False)
class CompoundNode(Statement):
    items: list[Node] = field(default_factory=list)
    end_offset: int = -1  # offset of the closing brace
    end_line: int = 0


@dataclass(eq=False)
class ExpressionStatementNode(Statement):
    expression: Optional[Node] = None


@dataclass(eq=False)
class IfNode(Statement):
    condition: Optional[Node] = None
    then_branch: Optional[Node] = None
    else_branch: Optional[Node] = None


@dataclass(eq=False)
class WhileNode(Statement):
    condition: Optional[Node] = None
    body: Optional[Node] = None


@dataclass(eq=False)
class DoWhileNode(Statement):
    body: Optional[Node] = None
    condition: Optional[Node] = None


@dataclass(eq=False)
class ForNode(Statement):
    init: Optional[Node] = None
    condition: Optional[Node] = None
    update: Optional[Node] = None
    body: Optional[Node] = None


@dataclass(eq=False)
class BreakNode(Statement):
    pass


@dataclass(eq=False)
class ContinueNode(Statement):
    pass


@dataclass(eq=False)
class ReturnNode(Statement):
    value: Optional[Node] = None


@dataclass(eq=False)
class ErrorStatementNode(Statement):
    """Placeholder produced by the parser's panic-mode recovery."""


# ----------------------------------------------------------------------------- expressions
@dataclass(eq=False)
class AssignmentNode(Expression):
    op: str = "="
    target: Optional[Node] = None
    value: Optional[Node] = None
    op_offset: int = -1
    op_length: int = 1


@dataclass(eq=False)
class BinaryOperationNode(Expression):
    op: str = "+"
    left: Optional[Node] = None
    right: Optional[Node] = None
    op_offset: int = -1


@dataclass(eq=False)
class UnaryOperationNode(Expression):
    """Prefix/postfix unary operation. ``op`` is one of ``- + ! & ++ --``."""

    op: str = "-"
    operand: Optional[Node] = None
    postfix: bool = False
    op_offset: int = -1


@dataclass(eq=False)
class FunctionCallNode(Expression):
    name: str = ""
    arguments: list[Node] = field(default_factory=list)
    lparen_offset: int = -1
    rparen_offset: int = -1


@dataclass(eq=False)
class ArrayAccessNode(Expression):
    array: Optional[Node] = None
    index: Optional[Node] = None


@dataclass(eq=False)
class IdentifierNode(Expression):
    name: str = ""


@dataclass(eq=False)
class LiteralNode(Expression):
    """``kind`` is one of ``int``, ``float``, ``char`` or ``string``."""

    kind: str = "int"
    value: Any = 0
    raw: str = ""


# ----------------------------------------------------------------------------- utilities
def walk(node: Node) -> Iterator[Node]:
    """Pre-order traversal of the tree rooted at ``node``."""
    yield node
    for child in node.children():
        yield from walk(child)


def _describe(node: Node) -> str:
    name = node.node_name
    extras = []
    for attr in ("name", "op", "type_name", "return_type", "kind", "raw"):
        if hasattr(node, attr):
            extras.append(f"{attr}={getattr(node, attr)!r}")
    pos = f" @{node.line}:{node.column}" if node.line else ""
    return f"{name}({', '.join(extras)}){pos}" if extras else f"{name}{pos}"


def format_tree(node: Node, indent: int = 0) -> str:
    """Return an indented textual rendering of the AST (used by the UI and docs)."""
    lines = ["  " * indent + _describe(node)]
    for child in node.children():
        lines.append(format_tree(child, indent + 1))
    return "\n".join(lines)
