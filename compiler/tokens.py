"""Token definitions shared by the lexer, parser and correction engine.

This module is independent of PLY so that it can be imported (and tested) anywhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# --------------------------------------------------------------------------- keywords
KEYWORDS: dict[str, str] = {
    "int": "INT", "float": "FLOAT", "double": "DOUBLE", "char": "CHAR", "void": "VOID",
    "if": "IF", "else": "ELSE", "for": "FOR", "while": "WHILE", "do": "DO",
    "break": "BREAK", "continue": "CONTINUE", "return": "RETURN",
}
TYPE_KEYWORDS = frozenset({"int", "float", "double", "char", "void"})
KEYWORD_TOKEN_TYPES = tuple(KEYWORDS.values())

# --------------------------------------------------------------------------- operators
OPERATOR_LEXEMES: dict[str, str] = {
    "PLUS": "+", "MINUS": "-", "TIMES": "*", "DIVIDE": "/", "MOD": "%",
    "LT": "<", "GT": ">", "LE": "<=", "GE": ">=", "EQ": "==", "NE": "!=",
    "LAND": "&&", "LOR": "||", "LNOT": "!", "AMP": "&",
    "ASSIGN": "=", "PLUS_ASSIGN": "+=", "MINUS_ASSIGN": "-=", "TIMES_ASSIGN": "*=",
    "DIVIDE_ASSIGN": "/=", "MOD_ASSIGN": "%=",
    "INC": "++", "DEC": "--",
}
DELIMITER_LEXEMES: dict[str, str] = {
    "LPAREN": "(", "RPAREN": ")", "LBRACE": "{", "RBRACE": "}",
    "LBRACKET": "[", "RBRACKET": "]", "SEMI": ";", "COMMA": ",",
}
LITERAL_TOKEN_TYPES = ("ID", "INT_CONST", "FLOAT_CONST", "CHAR_CONST", "STRING_LITERAL")

TOKEN_TYPES: tuple[str, ...] = (
    LITERAL_TOKEN_TYPES + KEYWORD_TOKEN_TYPES + tuple(OPERATOR_LEXEMES) + tuple(DELIMITER_LEXEMES)
)
LEXEME_BY_TYPE: dict[str, str] = {
    **OPERATOR_LEXEMES, **DELIMITER_LEXEMES, **{v: k for k, v in KEYWORDS.items()},
}
BINARY_OPERATOR_TYPES = frozenset({
    "PLUS", "MINUS", "TIMES", "DIVIDE", "MOD", "LT", "GT", "LE", "GE", "EQ", "NE", "LAND", "LOR",
    "ASSIGN", "PLUS_ASSIGN", "MINUS_ASSIGN", "TIMES_ASSIGN", "DIVIDE_ASSIGN", "MOD_ASSIGN",
})


@dataclass
class Token:
    """A lexical token with accurate source coordinates.

    The object intentionally exposes ``lineno`` / ``lexpos`` so that PLY's yacc driver
    can treat it exactly like a native ``LexToken``.
    """

    type: str
    value: str
    line: int
    column: int
    offset: int
    length: int = 0
    malformed: bool = False
    synthetic: bool = False  # inserted by the parser's repair mechanism

    @property
    def lineno(self) -> int:
        return self.line

    @property
    def lexpos(self) -> int:
        return self.offset

    @property
    def end_offset(self) -> int:
        return self.offset + self.length

    @property
    def lexeme(self) -> str:
        return self.value


def describe_token(tok: Optional[Token]) -> str:
    """Human-readable token description used in diagnostics."""
    if tok is None:
        return "end of file"
    if tok.type == "ID":
        return f"identifier '{tok.value}'"
    if tok.type in ("INT_CONST", "FLOAT_CONST"):
        return f"number '{tok.value}'"
    if tok.type == "CHAR_CONST":
        return f"character literal {tok.value}"
    if tok.type == "STRING_LITERAL":
        return "string literal"
    if tok.type in KEYWORD_TOKEN_TYPES:
        return f"keyword '{tok.value}'"
    return f"'{tok.value}'"


# --------------------------------------------------------------------------- literals
_STRICT_INT = re.compile(r"^(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]{0,2}$")
_STRICT_FLOAT = re.compile(r"^(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[fF]?$")
_PREFIX_FLOAT = re.compile(r"^(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", "'": "'", '"': '"',
            "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def classify_number(text: str) -> Optional[str]:
    """Return ``'int'``, ``'float'`` or ``None`` (malformed) for a numeric lexeme."""
    if _STRICT_INT.match(text):
        return "int"
    if _STRICT_FLOAT.match(text) and ("." in text or "e" in text.lower()):
        return "float"
    return None


def longest_valid_number_prefix(text: str) -> str:
    """Longest prefix of a malformed numeric lexeme that is a valid number (may be empty)."""
    hex_match = re.match(r"^0[xX][0-9a-fA-F]+", text)
    if hex_match:
        return hex_match.group(0)
    m = _PREFIX_FLOAT.match(text)
    return m.group(0) if m else ""


def parse_int_literal(text: str) -> int:
    """Convert an integer lexeme to ``int`` (0 for malformed text)."""
    t = text.rstrip("uUlL")
    try:
        if t.lower().startswith("0x"):
            return int(t, 16)
        if len(t) > 1 and t.startswith("0") and all(c in "01234567" for c in t):
            return int(t, 8)
        return int(t)
    except ValueError:
        return 0


def parse_float_literal(text: str) -> float:
    try:
        return float(text.rstrip("fF"))
    except ValueError:
        return 0.0


def decode_escapes(body: str) -> str:
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            out.append(_ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def decode_char_literal(text: str) -> str:
    body = text[1:-1] if text.endswith("'") and len(text) >= 2 else text[1:]
    decoded = decode_escapes(body)
    return decoded[:1]


def decode_string_literal(text: str) -> str:
    body = text[1:-1] if text.endswith('"') and len(text) >= 2 else text[1:]
    return decode_escapes(body)
