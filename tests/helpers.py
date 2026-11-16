"""Shared test helpers.

``needs_ply`` marks tests that exercise the PLY-based lexer/parser and therefore require the
``ply`` package. Everything else in the suite (AST, symbol table, type checker, semantic
analyzer, correction engine, code fixer, UI controller) is PLY-independent.
"""
from __future__ import annotations

import importlib.util
from functools import lru_cache
from pathlib import Path

import pytest

from compiler.ast_nodes import (ArrayAccessNode, AssignmentNode, BinaryOperationNode, BreakNode,
                                CompoundNode, DeclarationNode, DeclaratorNode, ExpressionStatementNode,
                                FunctionCallNode, FunctionNode, IdentifierNode, IfNode,
                                InitializerListNode, LiteralNode, ParameterNode, ProgramNode, ReturnNode,
                                UnaryOperationNode)
from compiler.error_manager import ErrorManager
from compiler.semantic_analyzer import SemanticAnalyzer

PLY_AVAILABLE = importlib.util.find_spec("ply") is not None
needs_ply = pytest.mark.skipif(not PLY_AVAILABLE, reason="PLY is not installed (pip install ply)")
SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def pipeline():
    from compiler.compiler_pipeline import CompilerPipeline
    return CompilerPipeline()


def analyze(source: str):
    return pipeline().analyze(source)


def kinds(result) -> list[str]:
    return [d.kind for d in result.diagnostics]


def ids(result) -> list[str]:
    return [d.error_id for d in result.diagnostics]


# ----------------------------------------------------------------- hand-built AST helpers
def num(v: int, **loc) -> LiteralNode:
    return LiteralNode(kind="int", value=v, raw=str(v), **loc)


def flt(v: float, raw: str | None = None, **loc) -> LiteralNode:
    return LiteralNode(kind="float", value=v, raw=raw or str(v), **loc)


def chr_(c: str, **loc) -> LiteralNode:
    return LiteralNode(kind="char", value=c, raw=f"'{c}'", **loc)


def string(s: str, **loc) -> LiteralNode:
    return LiteralNode(kind="string", value=s, raw=f'"{s}"', **loc)


def ident(n: str, **loc) -> IdentifierNode:
    return IdentifierNode(name=n, line=loc.pop("line", 1), column=loc.pop("column", 1), **loc)


def decl(t: str, name: str, init=None, array=None, size_omitted=False) -> DeclarationNode:
    d = DeclaratorNode(name=name, initializer=init, is_array=array is not None or size_omitted,
                       array_size=array, size_omitted=size_omitted, line=1, column=5)
    return DeclarationNode(type_name=t, declarators=[d], line=1, column=1)


def assign(target, value, op: str = "=") -> ExpressionStatementNode:
    return ExpressionStatementNode(expression=AssignmentNode(op=op, target=target, value=value, line=1, column=1))


def stmt(expr) -> ExpressionStatementNode:
    return ExpressionStatementNode(expression=expr)


def call(name: str, *args) -> FunctionCallNode:
    return FunctionCallNode(name=name, arguments=list(args), line=1, column=1)


def binop(op, a, b) -> BinaryOperationNode:
    return BinaryOperationNode(op=op, left=a, right=b, line=1, column=1)


def unop(op, a, postfix=False) -> UnaryOperationNode:
    return UnaryOperationNode(op=op, operand=a, postfix=postfix, line=1, column=1)


def index(arr, i) -> ArrayAccessNode:
    return ArrayAccessNode(array=arr, index=i, line=1, column=1)


def ret(value=None) -> ReturnNode:
    return ReturnNode(value=value, line=1, column=1)


def block(*items) -> CompoundNode:
    return CompoundNode(items=list(items), line=1, column=1, offset=0, end_offset=10)


def func(ret_type: str, name: str, params, *body) -> FunctionNode:
    ps = [ParameterNode(type_name=t, name=n, is_array=arr, line=1, column=1) for t, n, arr in params]
    return FunctionNode(return_type=ret_type, name=name, parameters=ps,
                        body=CompoundNode(items=list(body), line=1, column=1, offset=10, end_offset=99),
                        line=1, column=5)


def proto(ret_type: str, name: str, params) -> FunctionNode:
    ps = [ParameterNode(type_name=t, name=n, is_array=arr, line=1, column=1) for t, n, arr in params]
    return FunctionNode(return_type=ret_type, name=name, parameters=ps, body=None, line=1, column=5)


def main(*body) -> FunctionNode:
    return func("int", "main", [], *body, ret(num(0)))


def analyze_ast(*top_level):
    """Run the semantic analyzer on hand-built top-level nodes. Returns (errors, symbol_table)."""
    em = ErrorManager("\n" * 50)
    table = SemanticAnalyzer(em).analyze(ProgramNode(declarations=list(top_level)))
    return em, table


def sem_kinds(*top_level) -> list[str]:
    return [d.kind for d in analyze_ast(*top_level)[0].sorted()]


def brk() -> BreakNode:
    return BreakNode(line=1, column=1, offset=0)
