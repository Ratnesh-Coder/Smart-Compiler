"""Error catalogue and heuristic classification of parser failures.

The catalogue maps a machine-readable *kind* (e.g. ``missing_semicolon``) to its stable
identifier (``SYN001``), category, default severity and a generic explanation.
:func:`classify_syntax_error` turns the parser's view of a failure (offending token, previous
token, set of acceptable terminals) into one of the syntax kinds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.error_manager import ERROR, LEXICAL, SEMANTIC, SUGGESTION, SYNTAX, WARNING
from compiler.tokens import BINARY_OPERATOR_TYPES, Token, describe_token


@dataclass(frozen=True)
class ErrorSpec:
    kind: str
    error_id: str
    category: str
    severity: str
    title: str
    explanation: str


def _s(kind: str, eid: str, cat: str, sev: str, title: str, expl: str) -> tuple[str, ErrorSpec]:
    return kind, ErrorSpec(kind, eid, cat, sev, title, expl)


ERROR_CATALOG: dict[str, ErrorSpec] = dict([
    # ---- lexical
    _s("invalid_character", "LEX001", LEXICAL, ERROR, "Invalid character",
       "The character is not part of the C character set supported by this compiler, so the lexer "
       "cannot form a token from it."),
    _s("malformed_number", "LEX002", LEXICAL, ERROR, "Malformed numeric literal",
       "A numeric constant must be a valid integer, hexadecimal or floating-point literal. Letters, "
       "extra decimal points or an incomplete exponent make the literal invalid."),
    _s("unterminated_string", "LEX003", LEXICAL, ERROR, "Unterminated string literal",
       "A string literal must be closed by a double quote on the same line."),
    _s("unterminated_comment", "LEX004", LEXICAL, ERROR, "Unterminated block comment",
       "A block comment that starts with '/*' must be closed with '*/'. Everything up to the end of "
       "the file was consumed by the comment."),
    _s("invalid_char_literal", "LEX005", LEXICAL, ERROR, "Invalid character literal",
       "A character literal must contain exactly one character (or one escape sequence) between "
       "single quotes."),
    _s("preprocessor_ignored", "LEX006", LEXICAL, SUGGESTION, "Preprocessor directive ignored",
       "Preprocessor directives (such as #include) are not supported by this compiler subset; the line "
       "is ignored. printf and scanf are always available."),
    # ---- syntax
    _s("missing_semicolon", "SYN001", SYNTAX, ERROR, "Missing semicolon",
       "Statements and declarations in C must end with a semicolon."),
    _s("missing_rparen", "SYN002", SYNTAX, ERROR, "Missing closing parenthesis",
       "Every '(' must be matched by a ')'."),
    _s("missing_brace", "SYN003", SYNTAX, ERROR, "Missing brace",
       "Every '{' must be matched by a '}' and a function body must start with '{'."),
    _s("unexpected_token", "SYN004", SYNTAX, ERROR, "Unexpected token",
       "The parser found a token that cannot appear at this position according to the C grammar."),
    _s("unexpected_eof", "SYN005", SYNTAX, ERROR, "Unexpected end of file",
       "The source ended while a statement, expression or block was still incomplete."),
    _s("missing_operand", "SYN006", SYNTAX, ERROR, "Missing operand",
       "An operator needs an expression on each side (or a value after '=', '(' or ',')."),
    _s("missing_rbracket", "SYN008", SYNTAX, ERROR, "Missing closing bracket",
       "Every '[' must be matched by a ']'."),
    _s("invalid_assignment_target", "SYN009", SYNTAX, ERROR, "Invalid assignment target",
       "The left-hand side of an assignment must be a variable or an array element."),
    _s("invalid_call_target", "SYN010", SYNTAX, ERROR, "Invalid function call",
       "Only a function name can be called."),
    # ---- semantic
    _s("undeclared_identifier", "SEM001", SEMANTIC, ERROR, "Undeclared identifier",
       "An identifier must be declared before it is used."),
    _s("duplicate_declaration", "SEM002", SEMANTIC, ERROR, "Duplicate declaration",
       "A name can be declared only once per scope."),
    _s("type_mismatch", "SEM003", SEMANTIC, ERROR, "Type mismatch",
       "The value's type is not compatible with the type it is assigned to."),
    _s("incompatible_operands", "SEM004", SEMANTIC, ERROR, "Incompatible operands",
       "The operator cannot be applied to operands of these types."),
    _s("wrong_argument_count", "SEM005", SEMANTIC, ERROR, "Wrong number of arguments",
       "A call must supply exactly as many arguments as the function declares parameters."),
    _s("wrong_argument_type", "SEM006", SEMANTIC, ERROR, "Wrong argument type",
       "Each argument must be convertible to the corresponding parameter type."),
    _s("invalid_return", "SEM007", SEMANTIC, ERROR, "Invalid return",
       "The returned value must match the declared return type of the function."),
    _s("invalid_array_access", "SEM008", SEMANTIC, ERROR, "Invalid array access",
       "Only arrays can be indexed, and the index must be an integer within the declared bounds."),
    _s("void_expression_misuse", "SEM009", SEMANTIC, ERROR, "Void value used in an expression",
       "A function returning 'void' produces no value that can be used."),
    _s("undeclared_function", "SEM010", SEMANTIC, ERROR, "Undeclared function",
       "A function must be declared or defined before it is called."),
    _s("scope_violation", "SEM011", SEMANTIC, ERROR, "Identifier out of scope",
       "The identifier exists, but it was declared in a block that has already ended."),
    _s("break_continue_outside_loop", "SEM012", SEMANTIC, ERROR, "'break'/'continue' outside a loop",
       "'break' and 'continue' are only valid inside a loop."),
    _s("missing_return", "SEM013", SEMANTIC, WARNING, "Missing return statement",
       "A non-void function should return a value on every path."),
    _s("implicit_conversion", "SEM014", SEMANTIC, WARNING, "Implicit narrowing conversion",
       "Converting a floating-point value to an integer (or double to float) may lose information."),
    _s("conflicting_function", "SEM015", SEMANTIC, ERROR, "Conflicting function declaration",
       "A function may be declared repeatedly only with identical signatures and defined once."),
    _s("division_by_zero", "SEM016", SEMANTIC, WARNING, "Division by zero",
       "Dividing by the constant zero is undefined behaviour."),
    _s("format_mismatch", "SEM017", SEMANTIC, WARNING, "Format string mismatch",
       "The format specifiers do not match the number or types of the supplied arguments."),
    _s("invalid_declaration", "SEM018", SEMANTIC, ERROR, "Invalid declaration",
       "The declaration violates a rule of the supported C subset."),
    _s("not_callable", "SEM019", SEMANTIC, ERROR, "Not a function",
       "Only functions can be called."),
    _s("invalid_assignment", "SEM020", SEMANTIC, ERROR, "Invalid assignment",
       "Only modifiable variables and array elements can be assigned to."),
    _s("assignment_in_condition", "SEM022", SEMANTIC, WARNING, "Assignment used as a condition",
       "'=' assigns a value; '==' compares two values. Using '=' inside a condition is a common mistake."),
])


def get_spec(kind: str) -> ErrorSpec:
    try:
        return ERROR_CATALOG[kind]
    except KeyError as exc:
        raise KeyError(f"Unknown diagnostic kind {kind!r}") from exc


def all_error_ids() -> list[str]:
    return sorted(spec.error_id for spec in ERROR_CATALOG.values())


# --------------------------------------------------------------------------- classification
@dataclass
class SyntaxClassification:
    """Result of classifying a parser failure."""

    kind: str
    message: str
    description: str
    suggestion: str
    context: dict[str, Any] = field(default_factory=dict)


_OPERAND_EXPECTED_AFTER = BINARY_OPERATOR_TYPES | {"LPAREN", "LBRACKET", "COMMA", "LNOT", "RETURN"}
_OPERAND_TERMINATORS = {"SEMI", "RPAREN", "RBRACKET", "COMMA", "RBRACE"} | BINARY_OPERATOR_TYPES
_TOKEN_NAMES = {"SEMI": "';'", "RPAREN": "')'", "RBRACKET": "']'", "RBRACE": "'}'", "LBRACE": "'{'"}
_REPAIR_KINDS = {"SEMI": "missing_semicolon", "RPAREN": "missing_rparen", "RBRACKET": "missing_rbracket",
                 "RBRACE": "missing_brace", "LBRACE": "missing_brace"}


def classify_syntax_error(tok: Optional[Token], prev: Optional[Token], expected: list[str],
                          repair: Optional[str] = None, missing_count: int = 1) -> SyntaxClassification:
    """Choose the diagnostic kind and wording for a parser failure.

    ``repair`` is the terminal type (``SEMI``, ``RPAREN`` ...) the parser decided to insert, if any.
    """
    here = describe_token(tok)
    before = describe_token(prev) if prev is not None else "the start of the file"
    ctx: dict[str, Any] = {"expected": list(expected), "token": here, "previous": before}

    if repair:
        kind = _REPAIR_KINDS[repair]
        sym = _TOKEN_NAMES[repair]
        if repair == "SEMI":
            return SyntaxClassification(
                kind, f"Missing ';' before {here}",
                f"A statement or declaration must end with a semicolon. The parser reached {here} while the "
                f"statement that ends with {before} was still open.",
                f"Insert ';' right after {before}.", ctx)
        if repair == "RPAREN":
            return SyntaxClassification(
                kind, f"Missing ')' before {here}",
                "Parentheses are unbalanced: an opening '(' has no matching ')'.",
                f"Insert ')' right after {before}.", ctx)
        if repair == "RBRACKET":
            return SyntaxClassification(
                kind, f"Missing ']' before {here}",
                "Brackets are unbalanced: an opening '[' has no matching ']'.",
                f"Insert ']' right after {before}.", ctx)
        if repair == "LBRACE":
            return SyntaxClassification(
                kind, f"Missing '{{' before {here}",
                "A function body must be enclosed in braces, but the opening '{' is missing "
                "(there are more '}' than '{' in the file).",
                f"Insert '{{' after {before}.", ctx)
        plural = "s" if missing_count > 1 else ""
        ctx["missing_count"] = missing_count
        return SyntaxClassification(
            kind, f"Missing {missing_count} closing brace{plural} '}}' at end of file",
            "The file ended while a block was still open: an opening '{' has no matching '}'.",
            f"Insert '}}' to close the open block{plural}.", ctx)

    if tok is None:
        return SyntaxClassification(
            "unexpected_eof", "Unexpected end of file",
            "The source ended while a statement or expression was still incomplete.",
            "Complete the unfinished statement.", ctx)

    if prev is not None and prev.type in _OPERAND_EXPECTED_AFTER and tok.type in _OPERAND_TERMINATORS:
        return SyntaxClassification(
            "missing_operand", f"Missing operand between {before} and {here}",
            f"An expression is expected after {before}, but {here} was found instead.",
            "Supply the missing value or remove the dangling operator.", ctx)

    return SyntaxClassification(
        "unexpected_token", f"Unexpected {here}",
        f"The parser did not expect {here} after {before}.",
        f"Remove {here} or fix the preceding code.", ctx)
