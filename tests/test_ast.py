"""AST node tests (PLY independent)."""
from compiler.ast_nodes import (BinaryOperationNode, CompoundNode, IfNode, ProgramNode, format_tree,
                                walk)
from tests.helpers import assign, binop, block, decl, func, ident, main, num, ret


def test_nodes_have_keyword_only_location_defaults():
    n = num(5)
    assert (n.line, n.column, n.offset, n.length) == (0, 0, -1, 0)
    n2 = num(5, line=3, column=7, offset=40, length=1)
    assert (n2.line, n2.column, n2.offset) == (3, 7, 40)


def test_children_and_walk_cover_whole_tree():
    tree = ProgramNode(declarations=[main(decl("int", "x", num(1)), assign(ident("x"), binop("+", num(1), num(2))))])
    names = [n.node_name for n in walk(tree)]
    assert names[0] == "ProgramNode"
    for expected in ("FunctionNode", "CompoundNode", "DeclarationNode", "DeclaratorNode",
                     "AssignmentNode", "BinaryOperationNode", "LiteralNode", "IdentifierNode", "ReturnNode"):
        assert expected in names


def test_children_of_leaf_is_empty():
    assert list(num(1).children()) == []


def test_if_node_children_skip_missing_else():
    node = IfNode(condition=num(1), then_branch=block(), else_branch=None)
    assert [type(c) for c in node.children()] == [type(num(1)), CompoundNode]


def test_format_tree_renders_indented_structure():
    text = format_tree(binop("+", num(1), ident("a")))
    lines = text.splitlines()
    assert lines[0].startswith("BinaryOperationNode(op='+'")
    assert lines[1].startswith("  LiteralNode") and lines[2].startswith("  IdentifierNode")


def test_nodes_compare_by_identity():
    a, b = num(1), num(1)
    assert a != b and a == a


def test_tree_walk_visits_function_parameters():
    f = func("int", "f", [("int", "a", False), ("int", "b", False)], ret(ident("a")))
    assert sum(1 for n in walk(f) if n.node_name == "ParameterNode") == 2
