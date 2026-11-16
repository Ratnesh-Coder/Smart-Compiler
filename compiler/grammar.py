"""Grammar rules for the supported C subset, written for PLY Yacc (``ply.yacc``).

The rules live in :class:`CGrammar`; :mod:`compiler.parser` builds the LALR(1) parser from
this class. Productions are given in the ``p_*`` docstrings (PLY convention).

Operator precedence (lowest to highest) is declared in :attr:`CGrammar.precedence`:

    assignment (right) < || < && < == != < < <= > >= < + - < * / %

Unary operators and postfix operators are handled by dedicated non-terminals
(``unary_expression`` / ``postfix_expression``) and therefore bind tighter than every binary
operator. The dangling ``else`` is resolved by the usual ``LOWER_THAN_ELSE`` precedence trick.

NOTE: helper methods deliberately do not start with ``p_`` because PLY treats every such
attribute as a grammar rule.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from compiler.ast_nodes import (ArrayAccessNode, AssignmentNode, BinaryOperationNode, BreakNode,
                                CompoundNode, ContinueNode, DeclarationNode, DeclaratorNode,
                                DoWhileNode, ErrorStatementNode, ExpressionStatementNode, ForNode,
                                FunctionCallNode, FunctionNode, IdentifierNode, IfNode,
                                InitializerListNode, LiteralNode, Node, ParameterNode, ProgramNode,
                                ReturnNode, UnaryOperationNode, WhileNode)
from compiler.error_manager import ErrorManager
from compiler.tokens import (TOKEN_TYPES, decode_char_literal, decode_string_literal,
                             parse_float_literal, parse_int_literal)


def _tok_loc(tok: Any) -> dict[str, int]:
    return {"line": tok.line, "column": tok.column, "offset": tok.offset, "length": tok.length}


def _node_loc(node: Any) -> dict[str, int]:
    return {"line": node.line, "column": node.column, "offset": node.offset, "length": node.length}


class CGrammar:
    """Container for the PLY grammar productions."""

    tokens = TOKEN_TYPES
    start = "translation_unit"
    precedence = (
        ("nonassoc", "LOWER_THAN_ELSE"),
        ("nonassoc", "ELSE"),
        ("right", "ASSIGN", "PLUS_ASSIGN", "MINUS_ASSIGN", "TIMES_ASSIGN", "DIVIDE_ASSIGN",
         "MOD_ASSIGN"),
        ("left", "LOR"),
        ("left", "LAND"),
        ("left", "EQ", "NE"),
        ("left", "LT", "LE", "GT", "GE"),
        ("left", "PLUS", "MINUS"),
        ("left", "TIMES", "DIVIDE", "MOD"),
    )

    def __init__(self) -> None:
        self.errors: Optional[ErrorManager] = None
        self.error_handler: Callable[[Any], Any] = lambda tok: None
        self.recovered = False       # an `error` production fired (panic-mode recovery)
        self.action_errors = 0       # semantic checks performed inside grammar actions

    def reset(self, errors: ErrorManager) -> None:
        self.errors = errors
        self.recovered = False
        self.action_errors = 0

    def report_action_error(self, kind: str, node_or_tok: Any, message: str, description: str,
                            suggestion: str) -> None:
        """Report an error detected inside a grammar action (not a parse failure)."""
        self.action_errors += 1
        if self.errors is not None:
            self.errors.report(kind, node_or_tok.line, node_or_tok.column, message=message,
                               description=description, suggestion=suggestion,
                               offset=node_or_tok.offset, length=node_or_tok.length)

    # ======================================================================= program
    def p_translation_unit(self, p):
        """translation_unit : external_declaration_list"""
        first = p[1][0]
        p[0] = ProgramNode(declarations=p[1], line=first.line or 1, column=first.column or 1,
                           offset=max(first.offset, 0))

    def p_external_declaration_list(self, p):
        """external_declaration_list : external_declaration_list external_declaration
                                     | external_declaration"""
        p[0] = p[1] + [p[2]] if len(p) == 3 else [p[1]]

    def p_external_declaration(self, p):
        """external_declaration : function_definition
                                | function_declaration
                                | declaration"""
        p[0] = p[1]

    def p_external_declaration_error(self, p):
        """external_declaration : error SEMI"""
        self.recovered = True
        p[0] = ErrorStatementNode(**_tok_loc(p.slice[2]))

    # ======================================================================= declarations
    def p_type_specifier(self, p):
        """type_specifier : INT
                          | FLOAT
                          | DOUBLE
                          | CHAR
                          | VOID"""
        p[0] = (p[1], _tok_loc(p.slice[1]))

    def p_declaration(self, p):
        """declaration : type_specifier init_declarator_list SEMI"""
        name, loc = p[1]
        p[0] = DeclarationNode(type_name=name, declarators=p[2], type_offset=loc["offset"],
                               type_length=loc["length"], **loc)

    def p_init_declarator_list(self, p):
        """init_declarator_list : init_declarator_list COMMA init_declarator
                                | init_declarator"""
        p[0] = p[1] + [p[3]] if len(p) == 4 else [p[1]]

    def p_init_declarator_plain(self, p):
        """init_declarator : ID"""
        p[0] = DeclaratorNode(name=p[1], **_tok_loc(p.slice[1]))

    def p_init_declarator_init(self, p):
        """init_declarator : ID ASSIGN initializer"""
        p[0] = DeclaratorNode(name=p[1], initializer=p[3], **_tok_loc(p.slice[1]))

    def p_init_declarator_array(self, p):
        """init_declarator : ID LBRACKET expression RBRACKET"""
        p[0] = DeclaratorNode(name=p[1], is_array=True, array_size=p[3], **_tok_loc(p.slice[1]))

    def p_init_declarator_array_init(self, p):
        """init_declarator : ID LBRACKET expression RBRACKET ASSIGN initializer"""
        p[0] = DeclaratorNode(name=p[1], is_array=True, array_size=p[3], initializer=p[6],
                              **_tok_loc(p.slice[1]))

    def p_init_declarator_array_unsized(self, p):
        """init_declarator : ID LBRACKET RBRACKET ASSIGN initializer"""
        p[0] = DeclaratorNode(name=p[1], is_array=True, size_omitted=True, initializer=p[5],
                              **_tok_loc(p.slice[1]))

    def p_initializer(self, p):
        """initializer : expression
                       | LBRACE initializer_list RBRACE"""
        if len(p) == 2:
            p[0] = p[1]
        else:
            p[0] = InitializerListNode(elements=p[2], **_tok_loc(p.slice[1]))

    def p_initializer_list(self, p):
        """initializer_list : initializer_list COMMA expression
                            | expression"""
        p[0] = p[1] + [p[3]] if len(p) == 4 else [p[1]]

    # ======================================================================= functions
    def p_function_definition(self, p):
        """function_definition : type_specifier ID LPAREN RPAREN compound_statement
                               | type_specifier ID LPAREN parameter_list RPAREN compound_statement"""
        name, tloc = p[1]
        params = [] if len(p) == 6 else p[4]
        body = p[len(p) - 1]
        p[0] = FunctionNode(return_type=name, name=p[2], parameters=params, body=body,
                            type_offset=tloc["offset"], type_length=tloc["length"],
                            **_tok_loc(p.slice[2]))

    def p_function_declaration(self, p):
        """function_declaration : type_specifier ID LPAREN RPAREN SEMI
                                | type_specifier ID LPAREN parameter_list RPAREN SEMI"""
        name, tloc = p[1]
        params = [] if len(p) == 6 else p[4]
        p[0] = FunctionNode(return_type=name, name=p[2], parameters=params, body=None,
                            type_offset=tloc["offset"], type_length=tloc["length"],
                            **_tok_loc(p.slice[2]))

    def p_parameter_list(self, p):
        """parameter_list : parameter_list COMMA parameter
                          | parameter"""
        p[0] = p[1] + [p[3]] if len(p) == 4 else [p[1]]

    def p_parameter_type_only(self, p):
        """parameter : type_specifier"""
        name, loc = p[1]
        p[0] = ParameterNode(type_name=name, type_offset=loc["offset"], type_length=loc["length"], **loc)

    def p_parameter_named(self, p):
        """parameter : type_specifier ID"""
        name, loc = p[1]
        p[0] = ParameterNode(type_name=name, name=p[2], type_offset=loc["offset"],
                             type_length=loc["length"], **_tok_loc(p.slice[2]))

    def p_parameter_array(self, p):
        """parameter : type_specifier ID LBRACKET RBRACKET
                     | type_specifier ID LBRACKET expression RBRACKET"""
        name, loc = p[1]
        p[0] = ParameterNode(type_name=name, name=p[2], is_array=True, type_offset=loc["offset"],
                             type_length=loc["length"], **_tok_loc(p.slice[2]))

    # ======================================================================= statements
    def p_compound_statement(self, p):
        """compound_statement : LBRACE RBRACE
                              | LBRACE block_item_list RBRACE"""
        items = [] if len(p) == 3 else p[2]
        end = p.slice[len(p) - 1]
        p[0] = CompoundNode(items=items, end_offset=end.offset, end_line=end.line,
                            **_tok_loc(p.slice[1]))

    def p_block_item_list(self, p):
        """block_item_list : block_item_list block_item
                           | block_item"""
        p[0] = p[1] + [p[2]] if len(p) == 3 else [p[1]]

    def p_block_item(self, p):
        """block_item : declaration
                      | statement"""
        p[0] = p[1]

    def p_statement(self, p):
        """statement : compound_statement
                     | expression_statement
                     | selection_statement
                     | iteration_statement
                     | jump_statement"""
        p[0] = p[1]

    def p_statement_error(self, p):
        """statement : error SEMI"""
        self.recovered = True
        p[0] = ErrorStatementNode(**_tok_loc(p.slice[2]))

    def p_expression_statement_empty(self, p):
        """expression_statement : SEMI"""
        p[0] = ExpressionStatementNode(expression=None, **_tok_loc(p.slice[1]))

    def p_expression_statement(self, p):
        """expression_statement : expression SEMI"""
        p[0] = ExpressionStatementNode(expression=p[1], **_node_loc(p[1]))

    def p_selection_if(self, p):
        """selection_statement : IF LPAREN expression RPAREN statement %prec LOWER_THAN_ELSE"""
        p[0] = IfNode(condition=p[3], then_branch=p[5], **_tok_loc(p.slice[1]))

    def p_selection_if_else(self, p):
        """selection_statement : IF LPAREN expression RPAREN statement ELSE statement"""
        p[0] = IfNode(condition=p[3], then_branch=p[5], else_branch=p[7], **_tok_loc(p.slice[1]))

    def p_iteration_while(self, p):
        """iteration_statement : WHILE LPAREN expression RPAREN statement"""
        p[0] = WhileNode(condition=p[3], body=p[5], **_tok_loc(p.slice[1]))

    def p_iteration_do_while(self, p):
        """iteration_statement : DO statement WHILE LPAREN expression RPAREN SEMI"""
        p[0] = DoWhileNode(body=p[2], condition=p[5], **_tok_loc(p.slice[1]))

    def p_iteration_for(self, p):
        """iteration_statement : FOR LPAREN for_init opt_expression SEMI opt_expression RPAREN statement"""
        p[0] = ForNode(init=p[3], condition=p[4], update=p[6], body=p[8], **_tok_loc(p.slice[1]))

    def p_for_init_declaration(self, p):
        """for_init : declaration"""
        p[0] = p[1]

    def p_for_init_expression(self, p):
        """for_init : expression SEMI"""
        p[0] = ExpressionStatementNode(expression=p[1], **_node_loc(p[1]))

    def p_for_init_empty(self, p):
        """for_init : SEMI"""
        p[0] = None

    def p_opt_expression(self, p):
        """opt_expression : expression
                          | empty"""
        p[0] = p[1]

    def p_empty(self, p):
        """empty :"""
        p[0] = None

    def p_jump_break(self, p):
        """jump_statement : BREAK SEMI"""
        p[0] = BreakNode(**_tok_loc(p.slice[1]))

    def p_jump_continue(self, p):
        """jump_statement : CONTINUE SEMI"""
        p[0] = ContinueNode(**_tok_loc(p.slice[1]))

    def p_jump_return(self, p):
        """jump_statement : RETURN SEMI
                          | RETURN expression SEMI"""
        value = p[2] if len(p) == 4 else None
        p[0] = ReturnNode(value=value, **_tok_loc(p.slice[1]))

    # ======================================================================= expressions
    def p_expression_assignment(self, p):
        """expression : expression ASSIGN expression
                      | expression PLUS_ASSIGN expression
                      | expression MINUS_ASSIGN expression
                      | expression TIMES_ASSIGN expression
                      | expression DIVIDE_ASSIGN expression
                      | expression MOD_ASSIGN expression"""
        target, op_tok = p[1], p.slice[2]
        if not isinstance(target, (IdentifierNode, ArrayAccessNode)):
            self.report_action_error(
                "invalid_assignment_target", target, "Invalid assignment target",
                "The left-hand side of an assignment must be a variable or an array element.",
                "Assign to a variable (e.g. 'x') or an array element (e.g. 'a[i]').")
        p[0] = AssignmentNode(op=p[2], target=target, value=p[3], op_offset=op_tok.offset,
                              op_length=op_tok.length, **_node_loc(target))

    def p_expression_binary(self, p):
        """expression : expression LOR expression
                      | expression LAND expression
                      | expression EQ expression
                      | expression NE expression
                      | expression LT expression
                      | expression LE expression
                      | expression GT expression
                      | expression GE expression
                      | expression PLUS expression
                      | expression MINUS expression
                      | expression TIMES expression
                      | expression DIVIDE expression
                      | expression MOD expression"""
        p[0] = BinaryOperationNode(op=p[2], left=p[1], right=p[3], op_offset=p.slice[2].offset,
                                   **_node_loc(p[1]))

    def p_expression_unary(self, p):
        """expression : unary_expression"""
        p[0] = p[1]

    def p_unary_postfix(self, p):
        """unary_expression : postfix_expression"""
        p[0] = p[1]

    def p_unary_prefix(self, p):
        """unary_expression : MINUS unary_expression
                            | PLUS unary_expression
                            | LNOT unary_expression
                            | AMP unary_expression
                            | INC unary_expression
                            | DEC unary_expression"""
        tok = p.slice[1]
        p[0] = UnaryOperationNode(op=p[1], operand=p[2], postfix=False, op_offset=tok.offset,
                                  **_tok_loc(tok))

    def p_postfix_primary(self, p):
        """postfix_expression : primary_expression"""
        p[0] = p[1]

    def p_postfix_index(self, p):
        """postfix_expression : postfix_expression LBRACKET expression RBRACKET"""
        p[0] = ArrayAccessNode(array=p[1], index=p[3], **_node_loc(p[1]))

    def p_postfix_call(self, p):
        """postfix_expression : postfix_expression LPAREN RPAREN
                              | postfix_expression LPAREN argument_list RPAREN"""
        callee = p[1]
        args = [] if len(p) == 4 else p[3]
        if not isinstance(callee, IdentifierNode):
            self.report_action_error(
                "invalid_call_target", callee, "Invalid function call",
                "Only a function name can be called.", "Call a declared function by its name.")
            p[0] = callee
            return
        p[0] = FunctionCallNode(name=callee.name, arguments=args, lparen_offset=p.slice[2].offset,
                                rparen_offset=p.slice[len(p) - 1].offset, **_node_loc(callee))

    def p_postfix_incdec(self, p):
        """postfix_expression : postfix_expression INC
                              | postfix_expression DEC"""
        p[0] = UnaryOperationNode(op=p[2], operand=p[1], postfix=True, op_offset=p.slice[2].offset,
                                  **_node_loc(p[1]))

    def p_argument_list(self, p):
        """argument_list : argument_list COMMA expression
                         | expression"""
        p[0] = p[1] + [p[3]] if len(p) == 4 else [p[1]]

    def p_primary_identifier(self, p):
        """primary_expression : ID"""
        p[0] = IdentifierNode(name=p[1], **_tok_loc(p.slice[1]))

    def p_primary_int(self, p):
        """primary_expression : INT_CONST"""
        p[0] = LiteralNode(kind="int", value=parse_int_literal(p[1]), raw=p[1], **_tok_loc(p.slice[1]))

    def p_primary_float(self, p):
        """primary_expression : FLOAT_CONST"""
        p[0] = LiteralNode(kind="float", value=parse_float_literal(p[1]), raw=p[1], **_tok_loc(p.slice[1]))

    def p_primary_char(self, p):
        """primary_expression : CHAR_CONST"""
        p[0] = LiteralNode(kind="char", value=decode_char_literal(p[1]), raw=p[1], **_tok_loc(p.slice[1]))

    def p_primary_string(self, p):
        """primary_expression : STRING_LITERAL"""
        p[0] = LiteralNode(kind="string", value=decode_string_literal(p[1]), raw=p[1],
                           **_tok_loc(p.slice[1]))

    def p_primary_paren(self, p):
        """primary_expression : LPAREN expression RPAREN"""
        p[0] = p[2]

    # ======================================================================= errors
    def p_error(self, tok):
        """PLY error hook; delegates to the handler installed by :class:`compiler.parser.CParser`."""
        return self.error_handler(tok)
