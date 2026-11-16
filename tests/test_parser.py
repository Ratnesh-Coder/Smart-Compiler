"""Syntax analysis tests (require PLY)."""
import pytest

pytest.importorskip("ply")

from compiler.ast_nodes import (ArrayAccessNode, AssignmentNode, BinaryOperationNode, CompoundNode,  # noqa: E402
                                DeclarationNode, DoWhileNode, ForNode, FunctionCallNode, FunctionNode,
                                IdentifierNode, IfNode, LiteralNode, ProgramNode, ReturnNode,
                                UnaryOperationNode, WhileNode, walk)
from compiler.error_manager import ErrorManager  # noqa: E402
from compiler.lexer import CLexer  # noqa: E402
from compiler.parser import CParser  # noqa: E402
from tests.helpers import sample  # noqa: E402

_PARSER = None


def parse(src):
    global _PARSER
    if _PARSER is None:
        _PARSER = CParser()
    em = ErrorManager(src)
    toks = CLexer(em).tokenize(src)
    res = _PARSER.parse(toks, src, em)
    return res, em


def body(src):
    """Parse ``src`` inside ``int main() { ... }`` and return the statements."""
    res, em = parse("int main() {\n" + src + "\n}\n")
    assert em.error_count() == 0, [d.message for d in em]
    return res.ast.declarations[0].body.items


def expr(src):
    return body(f"x = {src};")[0].expression.value


# ===================================================================== valid programs
def test_global_and_local_declarations():
    res, em = parse("int a;\nfloat b = 1.5, c;\nint main() { char s[10]; int arr[3] = {1, 2, 3}; int u[] = {4}; }")
    assert em.error_count() == 0 and res.ast_reliable
    g1, g2, fn = res.ast.declarations
    assert isinstance(g1, DeclarationNode) and g1.type_name == "int"
    assert [d.name for d in g2.declarators] == ["b", "c"] and g2.declarators[0].initializer.kind == "float"
    s, arr, u = fn.body.items
    assert s.declarators[0].is_array and s.declarators[0].array_size.value == 10
    assert len(arr.declarators[0].initializer.elements) == 3
    assert u.declarators[0].size_omitted


def test_function_definition_prototype_and_void_parameters():
    res, em = parse("int add(int a, int b);\nvoid nothing(void);\nint add(int a, int b) { return a + b; }\n"
                    "int sum(int v[], int n) { return n; }\nint main() { return 0; }")
    assert em.error_count() == 0
    proto, void_proto, definition, arr_fn, _ = res.ast.declarations
    assert proto.body is None and [p.name for p in proto.parameters] == ["a", "b"]
    assert void_proto.parameters[0].name is None and void_proto.parameters[0].type_name == "void"
    assert isinstance(definition.body, CompoundNode) and definition.return_type == "int"
    assert arr_fn.parameters[0].is_array


def test_operator_precedence_multiplication_over_addition():
    e = expr("1 + 2 * 3")
    assert isinstance(e, BinaryOperationNode) and e.op == "+"
    assert isinstance(e.right, BinaryOperationNode) and e.right.op == "*"


def test_left_associativity_of_binary_operators():
    e = expr("a - b - c")
    assert e.op == "-" and e.left.op == "-" and isinstance(e.right, IdentifierNode) and e.right.name == "c"
    e = expr("a / b * c")
    assert e.op == "*" and e.left.op == "/"


def test_assignment_is_right_associative_and_lowest_precedence():
    stmt = body("a = b = c + 1;")[0].expression
    assert isinstance(stmt, AssignmentNode) and isinstance(stmt.value, AssignmentNode)
    assert stmt.value.value.op == "+"


def test_logical_and_relational_precedence():
    e = expr("a || b && c")
    assert e.op == "||" and e.right.op == "&&"
    e = expr("a == b < c")
    assert e.op == "==" and e.right.op == "<"
    e = expr("a + b < c * d")
    assert e.op == "<" and e.left.op == "+" and e.right.op == "*"


def test_parentheses_override_precedence():
    e = expr("(1 + 2) * 3")
    assert e.op == "*" and e.left.op == "+"


def test_unary_and_postfix_operators():
    e = expr("-a * b")
    assert e.op == "*" and isinstance(e.left, UnaryOperationNode) and e.left.op == "-"
    e = expr("!a && b")
    assert e.op == "&&" and e.left.op == "!"
    post = body("i++; --j;")
    assert post[0].expression.postfix and post[0].expression.op == "++"
    assert not post[1].expression.postfix and post[1].expression.op == "--"
    assert isinstance(expr("&a"), UnaryOperationNode)


def test_compound_assignment_operators():
    ops = [s.expression.op for s in body("a += 1; a -= 1; a *= 2; a /= 2; a %= 3;")]
    assert ops == ["+=", "-=", "*=", "/=", "%="]


def test_calls_and_array_access():
    e = expr("f(1, g(2), a[i + 1])")
    assert isinstance(e, FunctionCallNode) and e.name == "f" and len(e.arguments) == 3
    assert isinstance(e.arguments[1], FunctionCallNode)
    assert isinstance(e.arguments[2], ArrayAccessNode) and e.arguments[2].index.op == "+"
    assert expr("h()").arguments == []


def test_literal_values_are_decoded():
    assert expr("0x1F").value == 31 and expr("010").value == 8 and expr("2.5").kind == "float"
    assert expr("'\\n'").value == "\n" and expr("'a'").value == "a"
    assert expr('"hi\\tx"').value == "hi\tx" and expr('"s"').kind == "string"


def test_if_else_and_dangling_else_binds_to_nearest_if():
    outer = body("if (a) if (b) x = 1; else x = 2;")[0]
    assert isinstance(outer, IfNode) and outer.else_branch is None
    inner = outer.then_branch
    assert isinstance(inner, IfNode) and inner.else_branch is not None


def test_nested_if_else_chain():
    node = body("if (a) x = 1; else if (b) x = 2; else x = 3;")[0]
    assert isinstance(node.else_branch, IfNode) and node.else_branch.else_branch is not None


def test_loops():
    w, d, f1, f2, f3 = body("while (a) a--;\ndo { a++; } while (a < 3);\nfor (i = 0; i < 3; i++) x = i;\n"
                            "for (int j = 0; j < 3; j++) { }\nfor (;;) break;")
    assert isinstance(w, WhileNode) and isinstance(d, DoWhileNode)
    assert isinstance(f1, ForNode) and f1.condition.op == "<" and f1.update.postfix
    assert isinstance(f2.init, DeclarationNode)
    assert f3.init is None and f3.condition is None and f3.update is None


def test_jump_statements_and_empty_statement():
    items = body("break; continue; return; return 1 + 2; ;")
    assert [type(i).__name__ for i in items] == ["BreakNode", "ContinueNode", "ReturnNode", "ReturnNode",
                                                  "ExpressionStatementNode"]
    assert items[2].value is None and items[3].value.op == "+"


def test_nested_compound_statements():
    items = body("{ int a; { int b; } }")
    assert isinstance(items[0], CompoundNode) and isinstance(items[0].items[1], CompoundNode)


def test_source_locations_in_ast():
    res, _ = parse("int a;\n\nint compute(int n) {\n    return n * 2;\n}\n")
    fn = res.ast.declarations[1]
    assert isinstance(fn, FunctionNode) and (fn.line, fn.column) == (3, 5)
    ret = fn.body.items[0]
    assert isinstance(ret, ReturnNode) and (ret.line, ret.column) == (4, 5)
    assert (ret.value.line, ret.value.column) == (4, 12)
    assert fn.body.end_line == 5


def test_valid_sample_parses_without_diagnostics_and_covers_node_types():
    res, em = parse(sample("valid_program.c"))
    assert [d for d in em if d.severity == "error"] == [] and res.ast_reliable and res.repairs == 0
    found = {type(n).__name__ for n in walk(res.ast)}
    for name in ("FunctionNode", "ForNode", "WhileNode", "DoWhileNode", "IfNode", "FunctionCallNode",
                 "ArrayAccessNode", "AssignmentNode", "UnaryOperationNode", "BinaryOperationNode", "ReturnNode"):
        assert name in found


# ===================================================================== syntax errors
def test_missing_semicolon_is_one_precise_error_and_ast_survives():
    res, em = parse("int main() {\n    int x = 10\n    int y = 20;\n    return 0;\n}\n")
    (d,) = em.errors()
    assert d.error_id == "SYN001" and d.category == "Syntax"
    assert (d.line, d.column) == (2, 15)  # right after the "10"
    assert res.ast_reliable and res.repairs == 1
    assert len(res.ast.declarations[0].body.items) == 3  # both declarations and the return were recovered


def test_missing_semicolon_after_expression_statement_and_return():
    _, em = parse("int main() {\n    x = 1\n    return 0\n}\n")
    assert [(d.error_id, d.line) for d in em.errors()] == [("SYN001", 2), ("SYN001", 3)]


def test_missing_closing_parenthesis_in_condition_and_call():
    _, em = parse("int main() {\n    if (x > 5 {\n        x = 1;\n    }\n    printf(\"%d\", x;\n}\n")
    assert [(d.error_id, d.line) for d in em.errors()] == [("SYN002", 2), ("SYN002", 5)]


def test_missing_closing_bracket():
    _, em = parse("int main() {\n    int a[3;\n}\n")
    assert [d.error_id for d in em.errors()] == ["SYN008"]


def test_missing_closing_brace_at_end_of_file_reports_once():
    res, em = parse("int main() {\n    if (x) {\n        y = 1;\n")
    (d,) = em.errors()
    assert d.error_id == "SYN003" and d.context["missing_count"] == 2 and "2 closing braces" in d.message
    assert res.ast_reliable


def test_missing_opening_brace_of_function():
    _, em = parse("int main()\n    int x = 1;\n    return x;\n}\n")
    (d,) = em.errors()
    assert d.error_id == "SYN003" and "'{'" in d.message and d.line == 1


def test_unexpected_token():
    res, em = parse("int main() {\n    else x = 1;\n    return 0;\n}\n")
    (d,) = em.errors()
    assert d.error_id == "SYN004" and "else" in d.message and d.line == 2
    assert not res.ast_reliable and res.panic_recovery


def test_misspelt_type_keyword_is_one_syntax_error_without_cascade():
    res, em = parse("int main() {\n    itn x = 5;\n    int y = 6;\n    return 0;\n}\n")
    (d,) = em.errors()
    assert d.error_id == "SYN004" and "'x'" in d.message and d.line == 2
    assert res.panic_recovery


def test_missing_operand():
    _, em = parse("int main() {\n    int a = ;\n    x = 3 + ;\n}\n")
    assert [(d.error_id, d.line) for d in em.errors()] == [("SYN006", 2), ("SYN006", 3)]


def test_invalid_assignment_target_reported_from_grammar_action():
    res, em = parse("int main() {\n    1 = x;\n    (a + b) = 3;\n}\n")
    assert [d.error_id for d in em.errors()] == ["SYN009", "SYN009"] and not res.ast_reliable


def test_invalid_call_target():
    _, em = parse("int main() {\n    (a + b)(1);\n}\n")
    assert [d.error_id for d in em.errors()] == ["SYN010"]


def test_two_independent_errors_are_both_reported():
    _, em = parse("int main() {\n    int a = 1\n    if (a > 0 {\n        a = 2;\n    }\n    return 0;\n}\n")
    assert [(d.error_id, d.line) for d in em.errors()] == [("SYN001", 2), ("SYN002", 3)]


def test_error_recovery_continues_after_panic():
    _, em = parse("int main() {\n    else;\n    int a = 1\n    return 0;\n}\n")
    assert [(d.error_id, d.line) for d in em.errors()] == [("SYN004", 2), ("SYN001", 3)]


def test_syntax_error_sample_file():
    res, em = parse(sample("syntax_errors.c"))
    assert [(d.error_id, d.line) for d in em.errors()] == [("SYN001", 2), ("SYN002", 4), ("SYN004", 7), ("SYN002", 8)]


@pytest.mark.parametrize("src", [
    "int", "int main(", "int main() {", "}", "{", ")", "int 5;", "int main() { return; ; ; }}", ";;;",
    "int a[;", "if (", "for (;;", "int main() { for (int i = 0; i < 3 i++) {} }",
    "int x = = = 3;", "int main() { ((((((; }", "void f(int, ) {}", "else else else",
    "int main() { x = (1 + ; }", "int main() { a[ = 1; }",
])
def test_parser_never_crashes_on_malformed_input(src):
    res, em = parse(src)
    assert em.error_count() >= 1  # something was reported
    assert res.ast is None or isinstance(res.ast, ProgramNode)
