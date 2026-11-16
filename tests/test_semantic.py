"""Semantic analysis tests.

Sections 1-3 are PLY independent (type checker, symbol table, analyzer on hand-built ASTs).
Section 4 analyses real C source and therefore needs PLY.
"""
from compiler.ast_nodes import ForNode, WhileNode
from compiler.error_manager import ErrorManager
from compiler.symbol_table import FUNCTION, VARIABLE, Symbol, SymbolTable
from compiler.type_checker import (ERROR_TYPE, binary_result, check_assignment, common_type,
                                   describe_type, unary_result)
from tests.helpers import (analyze, analyze_ast, assign, binop, block, brk, call, chr_, decl, flt, func,
                           ident, index, kinds, main, needs_ply, num, proto, ret, sem_kinds, stmt,
                           string, unop)


# =========================================================== 1. type checker
def test_numeric_conversions_are_valid():
    for target, source in [("float", "int"), ("double", "int"), ("int", "char"), ("char", "int"),
                           ("double", "float"), ("float", "char")]:
        res = check_assignment(target, source)
        assert res.ok and not res.warning, (target, source)


def test_narrowing_float_to_int_is_a_warning_not_an_error():
    res = check_assignment("int", "double")
    assert res.ok and res.warning and res.kind == "float_to_int"
    assert check_assignment("float", "double").warning


def test_string_to_integer_is_error_with_expected_message():
    res = check_assignment("int", "char[]", source_is_string_literal=True)
    assert not res.ok
    assert res.reason == "Cannot assign a string literal to an integer variable."


def test_void_and_array_assignment_rules():
    assert not check_assignment("int", "void").ok
    assert check_assignment("char[]", "char[]").ok
    assert not check_assignment("int[]", "char[]").ok
    assert not check_assignment("int", "int[]").ok


def test_error_type_never_cascades():
    assert check_assignment("int", ERROR_TYPE).ok and check_assignment(ERROR_TYPE, "void").ok
    assert binary_result("+", ERROR_TYPE, "int")[0] == ERROR_TYPE


def test_binary_result_types():
    assert binary_result("+", "int", "int")[0] == "int"
    assert binary_result("+", "int", "double")[0] == "double"
    assert binary_result("*", "char", "char")[0] == "int"
    assert binary_result("<", "double", "int")[0] == "int"
    assert binary_result("&&", "int", "float")[0] == "int"
    assert common_type("float", "double") == "double"


def test_modulo_requires_integers():
    assert binary_result("%", "int", "int")[0] == "int"
    assert binary_result("%", "int", "float")[0] is None


def test_binary_rejects_arrays_and_void():
    assert binary_result("+", "int[]", "int")[0] is None
    assert binary_result("+", "void", "int")[0] is None
    assert binary_result("+", "char[]", "int")[0] is None


def test_unary_result_types():
    assert unary_result("-", "char")[0] == "int"
    assert unary_result("!", "double")[0] == "int"
    assert unary_result("++", "float")[0] == "float"
    assert unary_result("-", "int[]")[0] is None


def test_describe_type():
    assert describe_type("int[]") == "int array"
    assert describe_type("char[]").startswith("string")
    assert describe_type("int*") == "address of int"


# =========================================================== 2. symbol table
def test_symbol_table_nested_scopes_and_shadowing():
    t = SymbolTable()
    t.declare(Symbol("x", "int", VARIABLE, line=1))
    t.enter_scope("block")
    assert t.lookup("x").type_name == "int"
    assert t.declare(Symbol("x", "float", VARIABLE, line=2)) is None  # shadowing is legal
    assert t.lookup("x").type_name == "float"
    t.exit_scope()
    assert t.lookup("x").type_name == "int"


def test_symbol_table_duplicate_in_same_scope_returns_existing():
    t = SymbolTable()
    first = Symbol("a", "int", VARIABLE, line=1)
    assert t.declare(first) is None
    assert t.declare(Symbol("a", "float", VARIABLE, line=2)) is first
    assert t.lookup_local("a").type_name == "int"  # nothing overwritten


def test_symbol_table_records_scope_info_and_closed_scopes():
    t = SymbolTable()
    t.enter_scope("function f")
    t.declare(Symbol("local", "int", VARIABLE))
    sym = t.lookup("local")
    assert (sym.scope_name, sym.scope_level) == ("function f", 1)
    t.exit_scope()
    assert t.lookup("local") is None
    assert t.find_in_closed_scopes("local") is sym
    assert t.depth == 0


def test_cannot_exit_global_scope():
    try:
        SymbolTable().exit_scope()
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")


def test_visible_symbols_inner_shadows_outer():
    t = SymbolTable()
    t.declare(Symbol("v", "int", VARIABLE))
    t.enter_scope()
    t.declare(Symbol("v", "double", VARIABLE))
    t.declare(Symbol("w", "char", VARIABLE))
    vis = t.visible_symbols()
    assert vis["v"].type_name == "double" and "w" in vis


def test_builtins_are_declared_by_analyzer():
    _, table = analyze_ast(main())
    assert table.lookup_global("printf").category == FUNCTION
    assert table.lookup_global("scanf").variadic


# =========================================================== 3. analyzer (hand-built ASTs)
def test_valid_program_has_no_diagnostics():
    em, _ = analyze_ast(main(decl("int", "x", num(1)), decl("double", "y", binop("+", ident("x"), flt(2.5))),
                             assign(ident("x"), ident("x"), "+=")))
    assert len(em) == 0


def test_undeclared_variable_message_and_context():
    em, _ = analyze_ast(main(assign(ident("total"), num(100))))
    d = em.sorted()[0]
    assert d.error_id == "SEM001" and d.message == "Identifier 'total' has not been declared"
    assert d.context["name"] == "total" and d.context["inferred_type"] == "int"


def test_inferred_type_for_undeclared_follows_assigned_value():
    em, _ = analyze_ast(main(assign(ident("price"), flt(1.5))))
    assert em.sorted()[0].context["inferred_type"] == "double"


def test_duplicate_declaration_in_same_scope():
    assert sem_kinds(main(decl("int", "x"), decl("int", "x"))) == ["duplicate_declaration"]


def test_same_name_in_nested_scope_is_not_duplicate():
    assert sem_kinds(main(decl("int", "x"), block(decl("float", "x")))) == []


def test_string_assigned_to_int_declaration():
    em, _ = analyze_ast(main(decl("int", "age", string("hello"))))
    d = em.sorted()[0]
    assert d.error_id == "SEM003" and "string literal to an integer variable" in d.message


def test_char_array_string_initialiser_is_valid():
    assert sem_kinds(main(decl("char", "name", string("bob"), array=num(10)))) == []


def test_char_array_string_too_long():
    assert sem_kinds(main(decl("char", "n", string("toolong"), array=num(3)))) == ["invalid_declaration"]


def test_invalid_assignment_between_incompatible_types():
    kinds_ = sem_kinds(main(decl("int", "a"), decl("int", "arr", None, array=num(3)), assign(ident("a"), ident("arr"))))
    assert kinds_ == ["type_mismatch"]


def test_assign_to_array_name_is_invalid_assignment():
    assert sem_kinds(main(decl("int", "arr", None, array=num(3)), assign(ident("arr"), num(1)))) == ["invalid_assignment"]


def test_float_to_int_assignment_warns():
    em, _ = analyze_ast(main(decl("int", "i"), decl("double", "d"), assign(ident("i"), ident("d"))))
    assert [(d.kind, d.severity) for d in em] == [("implicit_conversion", "warning")]


def test_double_literal_to_float_is_silent():
    assert sem_kinds(main(decl("float", "f", flt(0.5)))) == []


def test_incompatible_operands():
    assert sem_kinds(main(decl("int", "a"), decl("int", "arr", None, array=num(2)),
                          stmt(binop("+", ident("a"), ident("arr"))))) == ["incompatible_operands"]
    assert sem_kinds(main(decl("double", "d"), stmt(binop("%", ident("d"), num(2))))) == ["incompatible_operands"]


def test_function_call_argument_count_mismatch():
    f = func("int", "add", [("int", "a", False), ("int", "b", False)], ret(binop("+", ident("a"), ident("b"))))
    em, _ = analyze_ast(f, main(stmt(call("add", num(1)))))
    d = em.sorted()[0]
    assert d.kind == "wrong_argument_count" and d.context["expected"] == 2 and d.context["got"] == 1


def test_function_call_argument_type_mismatch():
    f = func("int", "twice", [("int", "a", False)], ret(binop("*", ident("a"), num(2))))
    assert sem_kinds(f, main(stmt(call("twice", string("no"))))) == ["wrong_argument_type"]


def test_valid_call_with_numeric_conversion():
    f = func("double", "half", [("double", "a", False)], ret(binop("/", ident("a"), num(2))))
    assert sem_kinds(f, main(stmt(call("half", num(3))))) == []


def test_void_function_returning_value():
    f = func("void", "log", [], ret(num(1)))
    assert sem_kinds(f) == ["invalid_return"]


def test_nonvoid_function_returning_nothing_and_wrong_type():
    assert sem_kinds(func("int", "f", [], ret())) == ["invalid_return"]
    assert sem_kinds(func("int", "g", [], ret(string("x")))) == ["invalid_return"]


def test_missing_return_is_warning_but_main_is_exempt():
    em, _ = analyze_ast(func("int", "f", [], decl("int", "x")))
    assert [(d.kind, d.severity) for d in em] == [("missing_return", "warning")]
    em, _ = analyze_ast(func("int", "main", [], decl("int", "x")))
    assert len(em) == 0


def test_scope_violation_detected():
    assert sem_kinds(main(block(decl("int", "inner")), assign(ident("inner"), num(1)))) == ["scope_violation"]


def test_unknown_name_not_in_closed_scope_is_undeclared():
    assert sem_kinds(main(block(decl("int", "a")), assign(ident("b"), num(1)))) == ["undeclared_identifier"]


def test_array_index_rules():
    arr = decl("int", "a", None, array=num(3))
    assert sem_kinds(main(arr, stmt(index(ident("a"), num(1))))) == []
    assert sem_kinds(main(arr, stmt(index(ident("a"), flt(1.5))))) == ["invalid_array_access"]
    assert sem_kinds(main(decl("int", "n"), stmt(index(ident("n"), num(0))))) == ["invalid_array_access"]
    assert sem_kinds(main(arr, stmt(index(ident("a"), num(3))))) == ["invalid_array_access"]
    assert sem_kinds(main(arr, stmt(index(ident("a"), num(-1))))) == ["invalid_array_access"]


def test_array_declaration_rules():
    assert sem_kinds(main(decl("int", "a", None, array=num(0)))) == ["invalid_declaration"]
    assert sem_kinds(main(decl("int", "n"), decl("int", "a", None, array=ident("n")))) == ["invalid_declaration"]
    assert sem_kinds(main(decl("int", "a", None, array=binop("*", num(2), num(3))))) == []


def test_void_expression_misuse():
    f = func("void", "noop", [])
    assert "void_expression_misuse" in sem_kinds(f, main(decl("int", "x", call("noop"))))
    assert "void_expression_misuse" in sem_kinds(f, main(stmt(binop("+", call("noop"), num(1)))))


def test_void_variable_rejected():
    assert sem_kinds(main(decl("void", "v"))) == ["invalid_declaration"]


def test_break_continue_outside_loop():
    assert sem_kinds(main(brk())) == ["break_continue_outside_loop"]
    loop = WhileNode(condition=num(1), body=block(brk()), line=1, column=1)
    assert sem_kinds(main(loop)) == []


def test_for_loop_variable_is_scoped_to_loop():
    loop = ForNode(init=decl("int", "i", num(0)), condition=binop("<", ident("i"), num(3)),
                   update=unop("++", ident("i"), True), body=block(), line=1, column=1)
    assert sem_kinds(main(loop)) == []
    assert sem_kinds(main(loop, assign(ident("i"), num(1)))) == ["scope_violation"]


def test_undeclared_and_non_function_calls():
    assert sem_kinds(main(stmt(call("nope", num(1))))) == ["undeclared_function"]
    assert sem_kinds(main(decl("int", "x"), stmt(call("x")))) == ["not_callable"]


def test_function_used_before_declaration_is_error_like_c():
    later = func("int", "later", [], ret(num(1)))
    assert sem_kinds(main(stmt(call("later"))), later) == ["undeclared_function"]
    assert sem_kinds(proto("int", "later", []), main(stmt(call("later"))), later) == []


def test_conflicting_prototype_and_redefinition():
    assert sem_kinds(proto("int", "f", [("int", "a", False)]), func("int", "f", [("float", "a", False)], ret(num(1)))) == ["conflicting_function"]
    assert sem_kinds(func("int", "f", [], ret(num(1))), func("int", "f", [], ret(num(2)))) == ["conflicting_function"]


def test_parameters_share_scope_with_body():
    f = func("int", "f", [("int", "a", False)], decl("int", "a"), ret(num(1)))
    assert sem_kinds(f) == ["duplicate_declaration"]


def test_assignment_in_condition_warning_and_division_by_zero():
    from compiler.ast_nodes import IfNode
    cond = IfNode(condition=__import__("compiler.ast_nodes", fromlist=["AssignmentNode"]).AssignmentNode(
        op="=", target=ident("x"), value=num(1), line=1, column=1, op_offset=5), then_branch=block(), line=1, column=1)
    em, _ = analyze_ast(main(decl("int", "x"), cond))
    assert [d.kind for d in em] == ["assignment_in_condition"]
    assert sem_kinds(main(stmt(binop("/", num(4), num(0))))) == ["division_by_zero"]


def test_printf_format_checks():
    ok = stmt(call("printf", string("%d %f %c %s"), num(1), flt(2.0), chr_("a"), string("s")))
    assert sem_kinds(main(ok)) == []
    assert sem_kinds(main(stmt(call("printf", string("%d %d"), num(1))))) == ["format_mismatch"]
    assert sem_kinds(main(stmt(call("printf", string("%d"), flt(1.5))))) == ["format_mismatch"]
    assert sem_kinds(main(stmt(call("printf", num(3))))) == ["wrong_argument_type"]


def test_scanf_requires_address_of_scalar():
    x = decl("int", "x")
    assert sem_kinds(main(x, stmt(call("scanf", string("%d"), unop("&", ident("x")))))) == []
    em, _ = analyze_ast(main(x, stmt(call("scanf", string("%d"), ident("x")))))
    d = em.sorted()[0]
    assert d.kind == "wrong_argument_type" and d.context["missing_address_of"] is True


def test_errors_do_not_cascade():
    # one undeclared name used in a larger expression must yield exactly one diagnostic
    assert sem_kinds(main(decl("int", "y", binop("+", binop("*", ident("ghost"), num(2)), num(1))))) == ["undeclared_identifier"]


def test_diagnostics_carry_locations_and_ids():
    em, _ = analyze_ast(main(assign(ident("zz", line=7, column=3), num(1))))
    d = em.sorted()[0]
    assert (d.line, d.column) == (7, 3) and d.category == "Semantic" and d.severity == "error"


# =========================================================== 4. end-to-end from C source (PLY)
@needs_ply
def test_e2e_spec_examples():
    r = analyze('int main() {\n    int age = "hello";\n    return 0;\n}\n')
    assert r.diagnostics[0].message == "Cannot assign a string literal to an integer variable."
    r = analyze("int main() {\n    total = 100;\n    return 0;\n}\n")
    assert r.diagnostics[0].message == "Identifier 'total' has not been declared"


@needs_ply
def test_e2e_valid_conversions_produce_no_errors():
    src = ("int main() {\n int i = 'a';\n char c = 66;\n float f = 3;\n double d = f * 2 + i;\n"
           " float g = 2.5;\n d = g;\n return 0;\n}\n")
    assert analyze(src).error_count() == 0


@needs_ply
def test_e2e_semantic_sample_reports_every_category():
    from tests.helpers import sample
    got = set(kinds(analyze(sample("semantic_errors.c"))))
    for k in ("invalid_return", "type_mismatch", "undeclared_identifier", "duplicate_declaration",
              "wrong_argument_count", "invalid_array_access", "assignment_in_condition", "scope_violation",
              "undeclared_function", "break_continue_outside_loop"):
        assert k in got, k
