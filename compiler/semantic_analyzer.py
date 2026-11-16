"""Semantic analysis: symbol resolution, scope checking and type checking over the AST.

The analyzer walks the tree once, in source order (C requires declaration before use), and
reports every problem through :class:`compiler.error_manager.ErrorManager`. Expressions whose
error has already been reported evaluate to :data:`ERROR_TYPE`, which prevents cascades.

Diagnostics carry a machine-readable ``context`` (declaration offsets, visible symbols, expected
and actual types ...) so that the correction engine can build precise, position-based fixes
without re-deriving this information.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from compiler.ast_nodes import (ArrayAccessNode, AssignmentNode, BinaryOperationNode, BreakNode,
                                CompoundNode, ContinueNode, DeclarationNode, DoWhileNode,
                                ErrorStatementNode, ExpressionStatementNode, ForNode,
                                FunctionCallNode, FunctionNode, IdentifierNode, IfNode,
                                InitializerListNode, LiteralNode, Node, ProgramNode, ReturnNode,
                                UnaryOperationNode, WhileNode)
from compiler.error_manager import ErrorManager
from compiler.symbol_table import (ARRAY, FUNCTION, PARAMETER, VARIABLE, ParamInfo, Symbol,
                                   SymbolTable)
from compiler.type_checker import (ERROR_TYPE, STRING_TYPE, TypeCheckResult, binary_result, check_assignment,
                                   describe_type, element_type, is_array, is_integral, is_numeric,
                                   is_pointer, unary_result)

_FORMAT_RE = re.compile(
    r"%(?P<star>\*)?[-+ #0]*(?:\d+|\*)?(?:\.\d+)?(?P<mod>hh|h|ll|l|L|z|j|t)?"
    r"(?P<conv>[diouxXfFeEgGaAcspn%])")
_INT_CONVS = set("diouxXc")
_FLOAT_CONVS = set("fFeEgGaA")


class SemanticAnalyzer:
    """AST visitor performing symbol-table construction and type checking."""

    def __init__(self, errors: ErrorManager) -> None:
        self.errors = errors
        self.symbols = SymbolTable()
        self._function: Optional[Symbol] = None
        self._function_body_offset = -1
        self._loop_depth = 0
        self._declare_builtins()

    # ------------------------------------------------------------------ entry point
    def analyze(self, program: ProgramNode) -> SymbolTable:
        for item in program.declarations:
            self._visit(item)
        return self.symbols

    def _declare_builtins(self) -> None:
        for name in ("printf", "scanf"):
            self.symbols.declare(Symbol(name, "int", FUNCTION, params=[ParamInfo("char", "format", True)],
                                        variadic=True, is_builtin=True))

    # ------------------------------------------------------------------ reporting helpers
    def _report(self, kind: str, node: Node, message: str, description: Optional[str] = None,
                suggestion: Optional[str] = None, severity: Optional[str] = None, **context: Any) -> None:
        self.errors.report(kind, node.line, node.column, message=message, description=description,
                           suggestion=suggestion, offset=node.offset, length=node.length,
                           context=context, severity=severity)

    def _visible_context(self) -> list[dict[str, Any]]:
        return [s.brief() for s in self.symbols.visible_symbols().values()]

    # ------------------------------------------------------------------ dispatch
    def _visit(self, node: Optional[Node]) -> str:
        if node is None:
            return "void"
        method = getattr(self, "visit_" + type(node).__name__, None)
        if method is None:
            for child in node.children():
                self._visit(child)
            return ERROR_TYPE
        result = method(node)
        return result if isinstance(result, str) else "void"

    # ------------------------------------------------------------------ declarations
    def visit_DeclarationNode(self, node: DeclarationNode) -> None:
        for d in node.declarators:
            category = ARRAY if d.is_array else VARIABLE
            type_name = node.type_name
            if type_name == "void":
                self._report("invalid_declaration", d, f"Variable '{d.name}' cannot have type 'void'",
                             "'void' means 'no value', so a variable of that type cannot exist.",
                             "Use int, float, double or char instead.", reason="void_variable",
                             type_offset=node.type_offset, type_length=node.type_length)
                type_name = ERROR_TYPE
            size = self._array_size(d, node) if d.is_array else None
            sym = Symbol(d.name, type_name, category, line=d.line, column=d.column, offset=d.offset,
                         type_offset=node.type_offset, type_length=node.type_length,
                         is_array=d.is_array, array_size=size, initialized=d.initializer is not None)
            existing = self.symbols.declare(sym)
            if existing is not None:
                self._report("duplicate_declaration", d,
                             f"Duplicate declaration of '{d.name}' (first declared on line {existing.line})",
                             f"'{d.name}' is already declared in this scope; a name can be declared only once "
                             "per scope.", f"Remove the second declaration or choose a different name.",
                             name=d.name, previous_line=existing.line, type_offset=node.type_offset,
                             name_offset=d.offset, has_initializer=d.initializer is not None,
                             declarator_count=len(node.declarators), decl_offset=node.offset)
                continue
            if d.initializer is not None:
                self._check_initializer(d, node, sym)

    def _array_size(self, d, decl: DeclarationNode) -> Optional[int]:
        if d.size_omitted:
            init = d.initializer
            if isinstance(init, InitializerListNode):
                return len(init.elements)
            if isinstance(init, LiteralNode) and init.kind == "string":
                return len(init.value) + 1
            self._report("invalid_declaration", d, f"Array '{d.name}' needs a size or an initializer",
                         "An array declared with empty brackets must be initialised so its size is known.",
                         f"Write '{d.name}[N]' with a positive size.", reason="array_size")
            return None
        size_type = self._visit(d.array_size)
        value = self._const_int(d.array_size)
        if size_type != ERROR_TYPE and (not is_integral(size_type) or value is None):
            self._report("invalid_declaration", d.array_size or d,
                         f"Size of array '{d.name}' must be an integer constant",
                         "Variable-length arrays are not supported; use a positive integer constant.",
                         "Use a literal such as 10.", reason="array_size")
            return None
        if value is not None and value <= 0:
            self._report("invalid_declaration", d.array_size or d,
                         f"Size of array '{d.name}' must be positive (got {value})",
                         "An array needs at least one element.", "Use a positive size.", reason="array_size")
            return None
        return value

    def _const_int(self, node: Optional[Node]) -> Optional[int]:
        if isinstance(node, LiteralNode):
            if node.kind == "int":
                return int(node.value)
            if node.kind == "char" and node.value:
                return ord(node.value)
        if isinstance(node, UnaryOperationNode) and node.op in "-+" and not node.postfix:
            v = self._const_int(node.operand)
            return None if v is None else (-v if node.op == "-" else v)
        if isinstance(node, BinaryOperationNode) and node.op in ("+", "-", "*", "/", "%"):
            a, b = self._const_int(node.left), self._const_int(node.right)
            if a is None or b is None:
                return None
            if node.op in ("/", "%") and b == 0:
                return None
            return {"+": a + b, "-": a - b, "*": a * b, "/": int(a / b) if b else 0,
                    "%": a % b if b else 0}[node.op]
        return None

    def _check_initializer(self, d, decl: DeclarationNode, sym: Symbol) -> None:
        init = d.initializer
        if d.is_array:
            elem = decl.type_name
            if isinstance(init, InitializerListNode):
                for e in init.elements:
                    et = self._visit(e)
                    res = check_assignment(elem, et)
                    if not res.ok:
                        self._report("type_mismatch", e, f"Array initializer: {res.reason}", res.reason,
                                     f"Use values of type '{elem}'.", target_type=elem, source_type=et)
                if sym.array_size is not None and len(init.elements) > sym.array_size:
                    self._report("invalid_declaration", init,
                                 f"Too many initializers for array '{d.name}' "
                                 f"({len(init.elements)} for size {sym.array_size})",
                                 "The initializer list has more elements than the array can hold.",
                                 "Enlarge the array or remove extra values.", reason="too_many_initializers")
            elif isinstance(init, LiteralNode) and init.kind == "string":
                if elem != "char":
                    self._report("type_mismatch", init, f"Cannot initialise a {elem} array with a string literal",
                                 "Only 'char' arrays can be initialised from a string.",
                                 "Declare the array as 'char'.", target_type=elem + "[]", source_type=STRING_TYPE,
                                 source_is_string=True, **self._decl_target(sym))
                elif sym.array_size is not None and len(init.value) + 1 > sym.array_size:
                    self._report("invalid_declaration", init,
                                 f"String is too long for array '{d.name}' (needs {len(init.value) + 1} "
                                 f"elements, size is {sym.array_size})",
                                 "A string needs one extra element for the terminating '\\0'.",
                                 "Enlarge the array.", reason="too_many_initializers")
            else:
                it = self._visit(init)
                self._report("type_mismatch", init, f"Array '{d.name}' must be initialised with a "
                             "brace list or a string", "Scalar values cannot initialise an array.",
                             "Use '{...}'.", target_type=elem + "[]", source_type=it)
            return
        if isinstance(init, InitializerListNode):
            self._report("invalid_declaration", init, f"Scalar '{d.name}' cannot be initialised with braces",
                         "Braces are for array initialisation.", "Remove the braces or declare an array.",
                         reason="scalar_brace_init")
            return
        vt = self._visit(init)
        self._check_assignment_types(sym.type_name, vt, init, f"initialise '{d.name}'", sym, "declaration")

    def _decl_target(self, sym: Optional[Symbol]) -> dict[str, Any]:
        if sym is None:
            return {}
        return {"target_name": sym.name, "target_type_offset": sym.type_offset,
                "target_type_length": sym.type_length, "target_name_offset": sym.offset,
                "target_category": sym.category}

    # ------------------------------------------------------------------ functions
    def visit_FunctionNode(self, node: FunctionNode) -> None:
        params = [ParamInfo(p.type_name, p.name, p.is_array) for p in node.parameters]
        if len(params) == 1 and params[0].type_name == "void" and params[0].name is None:
            params = []
        sym = Symbol(node.name, node.return_type, FUNCTION, line=node.line, column=node.column,
                     offset=node.offset, type_offset=node.type_offset, type_length=node.type_length,
                     params=params, is_defined=node.body is not None)
        existing = self.symbols.lookup_global(node.name)
        if existing is not None:
            if existing.category != FUNCTION:
                self._report("conflicting_function", node,
                             f"'{node.name}' redeclared as a function (previously a {existing.category})",
                             "A name cannot be both a variable and a function.", "Rename one of them.")
                return
            if existing.signature() != sym.signature():
                self._report("conflicting_function", node, f"Conflicting types for function '{node.name}'",
                             f"It was previously declared as {self._sig_text(existing)} on line "
                             f"{existing.line or '?'}, now as {self._sig_text(sym)}.",
                             "Make the declarations identical.")
                return
            if node.body is not None and existing.is_defined and not existing.is_builtin:
                self._report("conflicting_function", node, f"Redefinition of function '{node.name}'",
                             "A function can only be defined once.", "Remove or rename one definition.")
                return
            if node.body is not None:
                existing.is_defined = True
            sym = existing
        else:
            self.symbols.declare(sym)
        if node.body is None:
            return

        previous = (self._function, self._function_body_offset)
        self._function, self._function_body_offset = sym, node.body.offset
        self.symbols.enter_scope(f"function {node.name}")
        for p in node.parameters:
            if p.type_name == "void" and not p.is_array:
                if len(node.parameters) == 1 and p.name is None:
                    continue
                self._report("invalid_declaration", p, "A parameter cannot have type 'void'",
                             "'void' is only valid as the sole, unnamed parameter list '(void)'.",
                             "Use a real type.", reason="void_parameter")
                continue
            if p.name is None:
                self._report("invalid_declaration", p, "Parameter name omitted in function definition",
                             "A function definition must name every parameter.", "Add a parameter name.",
                             reason="unnamed_parameter")
                continue
            psym = Symbol(p.name, p.type_name, PARAMETER, line=p.line, column=p.column, offset=p.offset,
                          type_offset=p.type_offset, type_length=p.type_length, is_array=p.is_array,
                          initialized=True)
            existing_p = self.symbols.declare(psym)
            if existing_p is not None:
                self._report("duplicate_declaration", p,
                             f"Duplicate parameter '{p.name}' (first declared on line {existing_p.line})",
                             "Two parameters cannot share a name.", "Rename one of them.",
                             name=p.name, previous_line=existing_p.line)
        for item in node.body.items:  # the body shares the parameter scope
            self._visit(item)
        if node.return_type != "void" and node.name != "main" and not self._always_returns(node.body):
            self._report("missing_return", node, f"Function '{node.name}' may end without returning a value",
                         f"'{node.name}' returns '{node.return_type}' but not every path ends with 'return'.",
                         "Add a return statement before the closing brace.", function=node.name,
                         return_type=node.return_type, body_end_offset=node.body.end_offset)
        self.symbols.exit_scope()
        self._function, self._function_body_offset = previous

    @staticmethod
    def _sig_text(s: Symbol) -> str:
        return f"{s.type_name} {s.name}({', '.join(p.value_type for p in s.params or [])})"

    def _always_returns(self, stmt: Optional[Node]) -> bool:
        if isinstance(stmt, ReturnNode):
            return True
        if isinstance(stmt, CompoundNode):
            return any(self._always_returns(i) for i in stmt.items)
        if isinstance(stmt, IfNode):
            return (stmt.else_branch is not None and self._always_returns(stmt.then_branch)
                    and self._always_returns(stmt.else_branch))
        return False

    # ------------------------------------------------------------------ statements
    def visit_CompoundNode(self, node: CompoundNode) -> None:
        self.symbols.enter_scope("block")
        for item in node.items:
            self._visit(item)
        self.symbols.exit_scope()

    def visit_ExpressionStatementNode(self, node: ExpressionStatementNode) -> None:
        if node.expression is not None:
            self._visit(node.expression)

    def visit_ErrorStatementNode(self, node: ErrorStatementNode) -> None:
        return None

    def _condition(self, expr: Optional[Node], what: str) -> None:
        if expr is None:
            return
        t = self._visit(expr)
        if isinstance(expr, AssignmentNode) and expr.op == "=":
            self._report("assignment_in_condition", expr,
                         f"Assignment '=' used as the condition of {what}",
                         "'=' stores a value; to compare use '=='. This is a very common typing mistake.",
                         "Replace '=' with '==' if a comparison was intended.",
                         op_offset=expr.op_offset, op_length=expr.op_length)
        if t == "void":
            self._report("void_expression_misuse", expr, f"A void value cannot be used as the condition of {what}")
        elif t != ERROR_TYPE and not is_numeric(t):
            self._report("incompatible_operands", expr,
                         f"The condition of {what} must be a numeric value, found {describe_type(t)}",
                         "Conditions must evaluate to a number (zero is false).", "Use a comparison.",
                         op="condition", left_type=t, right_type="")

    def visit_IfNode(self, node: IfNode) -> None:
        self._condition(node.condition, "'if'")
        self._visit(node.then_branch)
        if node.else_branch is not None:
            self._visit(node.else_branch)

    def visit_WhileNode(self, node: WhileNode) -> None:
        self._condition(node.condition, "'while'")
        self._loop_depth += 1
        self._visit(node.body)
        self._loop_depth -= 1

    def visit_DoWhileNode(self, node: DoWhileNode) -> None:
        self._loop_depth += 1
        self._visit(node.body)
        self._loop_depth -= 1
        self._condition(node.condition, "'do-while'")

    def visit_ForNode(self, node: ForNode) -> None:
        self.symbols.enter_scope("for")
        if node.init is not None:
            self._visit(node.init)
        self._condition(node.condition, "'for'")
        if node.update is not None:
            self._visit(node.update)
        self._loop_depth += 1
        self._visit(node.body)
        self._loop_depth -= 1
        self.symbols.exit_scope()

    def _loop_jump(self, node: Node, word: str) -> None:
        if self._loop_depth == 0:
            self._report("break_continue_outside_loop", node, f"'{word}' statement outside of a loop",
                         f"'{word}' only makes sense inside 'for', 'while' or 'do-while'.",
                         f"Remove the '{word}' or move it into a loop.", keyword=word, stmt_offset=node.offset)

    def visit_BreakNode(self, node: BreakNode) -> None:
        self._loop_jump(node, "break")

    def visit_ContinueNode(self, node: ContinueNode) -> None:
        self._loop_jump(node, "continue")

    def visit_ReturnNode(self, node: ReturnNode) -> None:
        fn = self._function
        if fn is None:
            return
        ctx = dict(function=fn.name, expected=fn.type_name, return_offset=node.offset,
                   func_type_offset=fn.type_offset, func_type_length=fn.type_length)
        vt = self._visit(node.value) if node.value is not None else None
        if fn.type_name == "void":
            if node.value is not None and vt != "void":
                self._report("invalid_return", node, f"Void function '{fn.name}' should not return a value",
                             f"'{fn.name}' is declared 'void', so 'return' must not have a value.",
                             "Remove the value or change the return type.", reason="void_returns_value",
                             got=vt, value_offset=node.value.offset, **ctx)
            return
        if node.value is None:
            self._report("invalid_return", node, f"Function '{fn.name}' must return a value of type '{fn.type_name}'",
                         "A non-void function must return a value.", "Return a value.",
                         reason="missing_value", **ctx)
            return
        res = self._convert(fn.type_name, vt or ERROR_TYPE, node.value)
        if not res.ok:
            self._report("invalid_return", node.value,
                         f"Cannot return {describe_type(vt or '?')} from function '{fn.name}' "
                         f"returning '{fn.type_name}'", res.reason, "Return a compatible value.",
                         reason="type", got=vt, **ctx)
        elif res.warning:
            self._report("implicit_conversion", node.value, f"Returning '{vt}' from a function returning "
                         f"'{fn.type_name}'", res.reason, "Change the return type or convert explicitly.",
                         target_type=fn.type_name, source_type=vt, func_type_offset=fn.type_offset,
                         func_type_length=fn.type_length, change_func_type=True)

    @staticmethod
    def _is_string(node: Optional[Node]) -> bool:
        return isinstance(node, LiteralNode) and node.kind == "string"

    def _convert(self, target: str, source: str, node: Optional[Node]):
        """check_assignment, except that a floating *literal* may initialise a float silently."""
        res = check_assignment(target, source, self._is_string(node))
        if res.warning and res.kind == "double_to_float" and isinstance(node, LiteralNode):
            return TypeCheckResult(True)
        return res

    # ------------------------------------------------------------------ expressions
    def visit_LiteralNode(self, node: LiteralNode) -> str:
        if node.kind == "float":
            return "float" if node.raw.endswith(("f", "F")) else "double"
        return {"int": "int", "char": "char", "string": STRING_TYPE}[node.kind]

    def visit_InitializerListNode(self, node: InitializerListNode) -> str:
        for e in node.elements:
            self._visit(e)
        return ERROR_TYPE

    def _undeclared(self, node: IdentifierNode, usage: str, inferred: str = "int") -> str:
        name = node.name
        closed = self.symbols.find_in_closed_scopes(name)
        ctx = dict(name=name, usage=usage, inferred_type=inferred, visible=self._visible_context(),
                   function_body_offset=self._function_body_offset, name_offset=node.offset)
        if closed is not None and closed.category != FUNCTION:
            self._report("scope_violation", node,
                         f"'{name}' is out of scope here (declared on line {closed.line} in a block that has ended)",
                         f"'{name}' was declared on line {closed.line}, but only exists inside the block "
                         "where it was declared.", "Declare the variable in a scope that encloses every use.",
                         declared_line=closed.line, **ctx)
        else:
            self._report("undeclared_identifier", node, f"Identifier '{name}' has not been declared",
                         f"'{name}' is used before any declaration of it is visible.",
                         f"Declare '{name}' before using it (e.g. '{inferred} {name};').", **ctx)
        return ERROR_TYPE

    def visit_IdentifierNode(self, node: IdentifierNode) -> str:
        sym = self.symbols.lookup(node.name)
        if sym is None:
            return self._undeclared(node, "read")
        if sym.category == FUNCTION:
            self._report("incompatible_operands", node, f"Function '{node.name}' cannot be used as a value",
                         "A function name must be followed by '(...)' to call it.",
                         f"Call it: {node.name}(...).", op="value", left_type="function", right_type="")
            return ERROR_TYPE
        return sym.value_type

    def visit_AssignmentNode(self, node: AssignmentNode) -> str:
        value_type = self._visit(node.value)
        target = node.target
        sym: Optional[Symbol] = None
        if isinstance(target, IdentifierNode):
            sym = self.symbols.lookup(target.name)
            if sym is None:
                inferred = value_type if is_numeric(value_type) else "int"
                self._undeclared(target, "write", inferred)
                return ERROR_TYPE
            if sym.category in (ARRAY, FUNCTION) or sym.is_array:
                what = "an array" if sym.category != FUNCTION else "a function"
                self._report("invalid_assignment", target, f"Cannot assign to '{sym.name}' because it is {what}",
                             "Arrays and functions are not modifiable values.",
                             "Assign to an element, e.g. 'a[0] = ...'." if what == "an array" else "Remove the assignment.")
                return ERROR_TYPE
            target_type = sym.value_type
        elif isinstance(target, ArrayAccessNode):
            target_type = self._visit(target)
        else:
            self._visit(target)
            return ERROR_TYPE
        if node.op == "=":
            self._check_assignment_types(target_type, value_type, node.value,
                                         "assign", sym, "assignment", node)
        else:
            op = node.op[:-1]
            res, reason = binary_result(op, target_type, value_type)
            if res is None:
                self._report("incompatible_operands", node, f"Invalid operands for '{node.op}'", reason,
                             "Use numeric operands.", op=node.op, left_type=target_type, right_type=value_type)
        return target_type

    def _check_assignment_types(self, target_type: str, value_type: str, value: Optional[Node],
                                action: str, sym: Optional[Symbol], where: str,
                                at: Optional[Node] = None) -> None:
        res = self._convert(target_type, value_type, value)
        anchor = value if value is not None else at
        if anchor is None:
            return
        base = dict(target_type=target_type, source_type=value_type, source_is_string=self._is_string(value),
                    value_offset=value.offset if value is not None else -1, where=where,
                    **self._decl_target(sym))
        if not res.ok:
            if res.kind == "void_value":
                self._report("void_expression_misuse", anchor, "A void value cannot be used here",
                             res.reason, "Use a function that returns a value.", **base)
            else:
                self._report("type_mismatch", anchor, res.reason, f"{res.reason} (target type '{target_type}', "
                             f"value type '{value_type}').",
                             f"Use a value of type '{target_type}'." if is_numeric(target_type) else "Use a compatible value.",
                             **base)
        elif res.warning:
            self._report("implicit_conversion", anchor, res.reason, "Implicit narrowing conversions silently "
                         "lose information.", "Use a wider target type or convert deliberately.", **base)

    def visit_BinaryOperationNode(self, node: BinaryOperationNode) -> str:
        lt, rt = self._visit(node.left), self._visit(node.right)
        if node.op in ("/", "%") and isinstance(node.right, LiteralNode) and node.right.kind == "int" \
                and node.right.value == 0:
            self._report("division_by_zero", node.right, "Division by constant zero",
                         "Dividing by zero is undefined behaviour.", "Use a non-zero divisor.")
        result, reason = binary_result(node.op, lt, rt)
        if result is None:
            kind = "void_expression_misuse" if "void" in (lt, rt) else "incompatible_operands"
            self._report(kind, node, f"Invalid operands to '{node.op}' ({describe_type(lt)} and "
                         f"{describe_type(rt)})", reason, "Use numeric operands.", op=node.op,
                         left_type=lt, right_type=rt)
            return ERROR_TYPE
        return result

    def visit_UnaryOperationNode(self, node: UnaryOperationNode) -> str:
        if node.op == "&":
            inner = node.operand
            if not isinstance(inner, (IdentifierNode, ArrayAccessNode)):
                self._visit(inner)
                self._report("incompatible_operands", node, "Cannot take the address of this expression",
                             "'&' needs a variable or array element.", "Apply '&' to a variable.",
                             op="&", left_type="expression", right_type="")
                return ERROR_TYPE
            t = self._visit(inner)
            return ERROR_TYPE if t == ERROR_TYPE else (t + "*" if not is_array(t) else t)
        t = self._visit(node.operand)
        if node.op in ("++", "--"):
            if not isinstance(node.operand, (IdentifierNode, ArrayAccessNode)):
                self._report("invalid_assignment", node, f"Operand of '{node.op}' must be a variable",
                             "Increment/decrement modify their operand.", "Apply it to a variable.")
                return ERROR_TYPE
            if is_array(t):
                self._report("invalid_assignment", node, f"Cannot apply '{node.op}' to an array",
                             "Arrays are not modifiable values.", "Apply it to an element.")
                return ERROR_TYPE
        result, reason = unary_result(node.op, t)
        if result is None:
            self._report("incompatible_operands", node, f"Invalid operand for unary '{node.op}'", reason,
                         "Use a numeric operand.", op=node.op, left_type=t, right_type="")
            return ERROR_TYPE
        return result

    def visit_ArrayAccessNode(self, node: ArrayAccessNode) -> str:
        base_t = self._visit(node.array)
        idx_t = self._visit(node.index)
        if idx_t != ERROR_TYPE and not is_integral(idx_t):
            self._report("invalid_array_access", node.index or node,
                         f"Array index must be an integer, found {describe_type(idx_t)}",
                         "Array subscripts are integers.", "Use an integer index.", reason="index_type")
        if base_t == ERROR_TYPE:
            return ERROR_TYPE
        if not is_array(base_t):
            name = node.array.name if isinstance(node.array, IdentifierNode) else "expression"
            self._report("invalid_array_access", node, f"'{name}' is not an array and cannot be indexed",
                         f"'{name}' has type {describe_type(base_t)}.", "Declare it as an array or remove '[...]'.",
                         reason="not_array")
            return ERROR_TYPE
        if isinstance(node.array, IdentifierNode):
            sym = self.symbols.lookup(node.array.name)
            idx = self._const_int(node.index)
            if sym is not None and sym.array_size is not None and idx is not None \
                    and (idx < 0 or idx >= sym.array_size):
                self._report("invalid_array_access", node.index or node,
                             f"Index {idx} is out of bounds for array '{sym.name}' of size {sym.array_size}",
                             f"Valid indices are 0..{sym.array_size - 1}.", "Use an index inside the bounds.",
                             reason="out_of_bounds", size=sym.array_size, index=idx)
        return element_type(base_t)

    # ------------------------------------------------------------------ calls
    def visit_FunctionCallNode(self, node: FunctionCallNode) -> str:
        sym = self.symbols.lookup(node.name)
        arg_types = [self._visit(a) for a in node.arguments]
        call_ctx = dict(function=node.name, call_offset=node.offset, lparen_offset=node.lparen_offset,
                        rparen_offset=node.rparen_offset)
        if sym is None:
            funcs = [s.brief() for s in self.symbols.visible_symbols().values() if s.category == FUNCTION]
            self._report("undeclared_function", node, f"Function '{node.name}' has not been declared",
                         f"'{node.name}' is called before any declaration of it is visible.",
                         "Declare or define the function before calling it, or fix the name.",
                         name=node.name, visible=self._visible_context(), functions=funcs,
                         arg_count=len(node.arguments), arg_types=arg_types, name_offset=node.offset)
            return ERROR_TYPE
        if sym.category != FUNCTION:
            self._report("not_callable", node, f"'{node.name}' is not a function",
                         f"'{node.name}' is a {sym.category} and cannot be called.", "Remove the parentheses.")
            return ERROR_TYPE
        for a, t in zip(node.arguments, arg_types):
            if t == "void":
                self._report("void_expression_misuse", a, "A void value cannot be passed as an argument")
        if sym.variadic:
            self._check_variadic(node, sym, arg_types, call_ctx)
            return sym.type_name
        params = sym.params or []
        if len(node.arguments) != len(params):
            self._report("wrong_argument_count", node,
                         f"Function '{node.name}' expects {len(params)} argument(s) but {len(node.arguments)} "
                         "were given", f"'{node.name}' is declared as {self._sig_text(sym)}.",
                         "Match the number of arguments to the declaration.", expected=len(params),
                         got=len(node.arguments), param_types=[p.value_type for p in params], **call_ctx)
        else:
            for i, (a, t, p) in enumerate(zip(node.arguments, arg_types, params)):
                res = self._convert(p.value_type, t, a)
                if not res.ok and t != "void":
                    self._report("wrong_argument_type", a,
                                 f"Argument {i + 1} of '{node.name}': {res.reason}",
                                 f"Parameter {i + 1} has type '{p.value_type}', the argument has type "
                                 f"{describe_type(t)}.", f"Pass a value of type '{p.value_type}'.",
                                 arg_index=i, expected=p.value_type, got=t, arg_offset=a.offset, **call_ctx)
                elif res.warning:
                    self._report("implicit_conversion", a, f"Argument {i + 1} of '{node.name}': {res.reason}",
                                 "The argument is implicitly narrowed.", "Pass a matching type.",
                                 target_type=p.value_type, source_type=t, value_offset=a.offset)
        return sym.type_name

    def _check_variadic(self, node: FunctionCallNode, sym: Symbol, arg_types: list[str],
                        call_ctx: dict[str, Any]) -> None:
        if not node.arguments:
            self._report("wrong_argument_count", node, f"'{node.name}' needs at least a format string",
                         "The first argument must be the format string.", "Add a format string.",
                         expected=1, got=0, param_types=["char[]"], **call_ctx)
            return
        if arg_types[0] not in (STRING_TYPE, ERROR_TYPE):
            self._report("wrong_argument_type", node.arguments[0],
                         f"The first argument of '{node.name}' must be a format string",
                         f"It has type {describe_type(arg_types[0])}.", "Pass a string literal first.",
                         arg_index=0, expected="char[]", got=arg_types[0], arg_offset=node.arguments[0].offset,
                         **call_ctx)
            return
        is_scanf = node.name == "scanf"
        if is_scanf:
            for i, (a, t) in enumerate(zip(node.arguments[1:], arg_types[1:]), start=1):
                if t != ERROR_TYPE and not is_pointer(t) and not is_array(t):
                    self._report("wrong_argument_type", a,
                                 f"'scanf' needs the address of a variable for argument {i + 1} (missing '&'?)",
                                 "scanf stores the value through a pointer, so a plain variable will not work.",
                                 "Write '&' before the variable name.", arg_index=i, expected="address",
                                 got=t, missing_address_of=True, arg_offset=a.offset, **call_ctx)
        fmt = node.arguments[0]
        if not self._is_string(fmt):
            return
        specs = [m for m in _FORMAT_RE.finditer(fmt.value) if m.group("conv") != "%"
                 and not (is_scanf and m.group("star"))]
        args = list(zip(node.arguments[1:], arg_types[1:]))
        if len(specs) != len(args):
            self._report("format_mismatch", node,
                         f"Format string of '{node.name}' has {len(specs)} conversion(s) but "
                         f"{len(args)} argument(s) were supplied",
                         "Each conversion specifier consumes exactly one argument.",
                         "Make the numbers match.", expected=len(specs), got=len(args), **call_ctx)
            return
        for m, (a, t) in zip(specs, args):
            if t == ERROR_TYPE:
                continue
            conv, mod = m.group("conv"), m.group("mod") or ""
            actual = t[:-1] if (is_scanf and is_pointer(t)) else t
            problem = None
            if conv in _INT_CONVS:
                if is_scanf and conv != "c":
                    if actual != "int":
                        problem = "an int"
                elif not is_integral(actual):
                    problem = "an integer"
            elif conv in _FLOAT_CONVS:
                if is_scanf:
                    need = "double" if mod == "l" else "float"
                    if actual != need:
                        problem = f"a {need}"
                elif actual not in ("float", "double"):
                    problem = "a floating-point value"
            elif conv == "s" and not (is_array(t) or (is_scanf and is_array(actual))):
                problem = "a string"
            if problem:
                self._report("format_mismatch", a, f"Format '%{mod}{conv}' expects {problem} but the argument has "
                             f"type {describe_type(t)}", "The specifier and the argument type disagree, which "
                             "gives wrong output or undefined behaviour.",
                             "Use the specifier that matches the argument type.",
                             spec=f"%{mod}{conv}", got=t, arg_offset=a.offset)
