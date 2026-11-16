"""Lexical analysis built on PLY Lex (``ply.lex``).

Produces a list of :class:`compiler.tokens.Token` objects with accurate 1-based line/column
information and reports lexical errors through :class:`compiler.error_manager.ErrorManager`.

Error handling strategy (nothing is ever silently discarded):

* invalid characters are reported (LEX001) and skipped;
* malformed numbers, unterminated strings and bad character literals are reported and still
  emitted as (flagged) constant tokens so that the parser can continue;
* an unterminated block comment is reported (LEX004) and consumes the rest of the file.
"""
from __future__ import annotations

import re
from typing import Any, Optional

try:  # PLY is a mandatory dependency of the lexical analyser
    from ply import lex
    from ply.lex import TOKEN
except ImportError as exc:  # pragma: no cover - exercised only when PLY is missing
    raise ImportError("PLY is required for lexical analysis. Install it with: pip install ply") from exc

from compiler.error_manager import ErrorManager
from compiler.tokens import KEYWORDS, TOKEN_TYPES, Token, classify_number, longest_valid_number_prefix
from utils.source_location import LineIndex

# --------------------------------------------------------------------------- regular expressions
NEWLINE_RE = r"\n+"
LINE_COMMENT_RE = r"//[^\n]*"
BLOCK_COMMENT_RE = r"/\*(?:.|\n)*?\*/"
UNTERMINATED_COMMENT_RE = r"/\*(?:.|\n)*"
STRING_RE = r'"(?:[^"\\\n]|\\.)*"'
UNTERMINATED_STRING_RE = r'"(?:[^"\\\n]|\\.)*\\?'
CHAR_RE = r"'(?:[^'\\\n]|\\.)'"
BAD_CHAR_RE = r"'(?:[^'\\\n]|\\.)*'?"
# A numeric candidate is the longest run of digits/letters/dots; validity is checked afterwards
NUMBER_RE = r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?[A-Za-z0-9_.]*"
IDENTIFIER_RE = r"[A-Za-z_][A-Za-z0-9_]*"
PREPROCESSOR_RE = r"\#[^\n]*"


def _printable(ch: str) -> str:
    return ch if ch.isprintable() and not ch.isspace() else f"U+{ord(ch):04X}"


class CLexer:
    """PLY lexer for the supported C subset (object-style PLY usage)."""

    tokens = TOKEN_TYPES
    t_ignore = " \t\r"

    # --- operators (string rules: PLY sorts them by decreasing regex length) ------------
    t_PLUS = r"\+"
    t_MINUS = r"-"
    t_TIMES = r"\*"
    t_DIVIDE = r"/"
    t_MOD = r"%"
    t_LE = r"<="
    t_GE = r">="
    t_EQ = r"=="
    t_NE = r"!="
    t_LT = r"<"
    t_GT = r">"
    t_LAND = r"&&"
    t_LOR = r"\|\|"
    t_LNOT = r"!"
    t_AMP = r"&"
    t_ASSIGN = r"="
    t_PLUS_ASSIGN = r"\+="
    t_MINUS_ASSIGN = r"-="
    t_TIMES_ASSIGN = r"\*="
    t_DIVIDE_ASSIGN = r"/="
    t_MOD_ASSIGN = r"%="
    t_INC = r"\+\+"
    t_DEC = r"--"
    # --- delimiters -------------------------------------------------------------------
    t_LPAREN = r"\("
    t_RPAREN = r"\)"
    t_LBRACE = r"\{"
    t_RBRACE = r"\}"
    t_LBRACKET = r"\["
    t_RBRACKET = r"\]"
    t_SEMI = r";"
    t_COMMA = r","

    def __init__(self, errors: Optional[ErrorManager] = None) -> None:
        self.errors = errors if errors is not None else ErrorManager()
        self._source = ""
        self._index = LineIndex("")
        self._lexer: Any = None

    # ------------------------------------------------------------------ PLY function rules
    # (function rules are matched in definition order, before the string rules above)
    @TOKEN(NEWLINE_RE)
    def t_newline(self, t):
        t.lexer.lineno += len(t.value)

    @TOKEN(LINE_COMMENT_RE)
    def t_line_comment(self, t):
        pass  # comments are discarded

    @TOKEN(BLOCK_COMMENT_RE)
    def t_block_comment(self, t):
        t.lexer.lineno += t.value.count("\n")

    @TOKEN(UNTERMINATED_COMMENT_RE)
    def t_unterminated_comment(self, t):
        t.lexer.lineno += t.value.count("\n")
        self._report("unterminated_comment", t.lexpos, 2,
                     "Unterminated block comment",
                     suggestion="Close the comment with '*/'.",
                     comment_end=t.lexpos + len(t.value))

    @TOKEN(PREPROCESSOR_RE)
    def t_preprocessor(self, t):
        # Preprocessor lines are not part of the supported subset: note and skip them.
        self._report("preprocessor_ignored", t.lexpos, len(t.value),
                     f"Preprocessor directive '{t.value.strip()}' is ignored",
                     suggestion="No action needed: printf and scanf are built in.", lexeme=t.value)

    @TOKEN(STRING_RE)
    def t_STRING_LITERAL(self, t):
        return t

    @TOKEN(UNTERMINATED_STRING_RE)
    def t_unterminated_string(self, t):
        self._report("unterminated_string", t.lexpos, len(t.value),
                     "Unterminated string literal",
                     suggestion='Add the closing double quote (") to the string.',
                     lexeme=t.value)
        t.type = "STRING_LITERAL"
        t.malformed = True
        return t

    @TOKEN(CHAR_RE)
    def t_CHAR_CONST(self, t):
        return t

    @TOKEN(BAD_CHAR_RE)
    def t_bad_char_literal(self, t):
        text = t.value
        if text == "''":
            reason, msg = "empty", "Empty character literal"
            hint = "Put exactly one character between the single quotes, e.g. 'a'."
        elif text.endswith("'") and len(text) > 2:
            reason, msg = "multi", f"Character literal {text} contains more than one character"
            hint = f"Use double quotes for text (\"{text[1:-1]}\") or keep a single character."
        else:
            reason, msg = "unterminated", "Unterminated character literal"
            hint = "Add the closing single quote (') after the character."
        self._report("invalid_char_literal", t.lexpos, len(text), msg, suggestion=hint,
                     reason=reason, lexeme=text)
        t.type = "CHAR_CONST"
        t.malformed = True
        return t

    @TOKEN(NUMBER_RE)
    def t_number(self, t):
        kind = classify_number(t.value)
        if kind is None:
            self._report_malformed_number(t)
            t.malformed = True
            t.type = "FLOAT_CONST" if "." in t.value else "INT_CONST"
        else:
            t.type = "INT_CONST" if kind == "int" else "FLOAT_CONST"
        return t

    @TOKEN(IDENTIFIER_RE)
    def t_ID(self, t):
        t.type = KEYWORDS.get(t.value, "ID")
        return t

    def t_error(self, t):
        ch = t.value[0]
        shown = _printable(ch)
        self._report("invalid_character", t.lexpos, 1, f"Invalid character '{shown}'",
                     description=f"The character '{shown}' is not part of the supported C character set.",
                     suggestion="Remove the invalid character if it was entered accidentally.",
                     char=ch)
        t.lexer.skip(1)

    # ------------------------------------------------------------------ helpers
    def _report(self, kind: str, offset: int, length: int, message: str,
                description: Optional[str] = None, suggestion: Optional[str] = None,
                **context: Any) -> None:
        line, col = self._index.locate(offset)
        self.errors.report(kind, line, col, message=message, description=description,
                           suggestion=suggestion, offset=offset, length=length, context=context)

    def _report_malformed_number(self, t) -> None:
        text = t.value
        if re.match(r"^\d+[A-Za-z_]", text) and "." not in text:
            detail = "An identifier cannot start with a digit, and a number cannot contain letters."
        elif text.count(".") > 1:
            detail = "A floating-point literal can contain only one decimal point."
        elif re.search(r"[eE][+-]?$", text):
            detail = "The exponent of a floating-point literal needs at least one digit."
        else:
            detail = "The literal mixes digits with characters that are not allowed in a number."
        prefix = longest_valid_number_prefix(text)
        hint = (f"Use a valid number such as '{prefix}', or rename it if it was meant to be an identifier."
                if prefix else "Write a valid numeric constant.")
        self._report("malformed_number", t.lexpos, len(text), f"Malformed numeric literal '{text}'",
                     description=detail, suggestion=hint, lexeme=text, valid_prefix=prefix)

    # ------------------------------------------------------------------ public API
    def build(self, **kwargs: Any) -> None:
        """Build the underlying PLY lexer."""
        self._lexer = lex.lex(module=self, reflags=re.UNICODE, **kwargs)

    def tokenize(self, source: str) -> list[Token]:
        """Tokenise ``source`` and return the token list (diagnostics go to ``self.errors``)."""
        if self._lexer is None:
            self.build()
        self._source = source
        self._index = LineIndex(source)
        self.errors.set_source(source)
        self._lexer.input(source)
        self._lexer.lineno = 1
        tokens: list[Token] = []
        while True:
            tok = self._lexer.token()
            if tok is None:
                break
            line, col = self._index.locate(tok.lexpos)
            tokens.append(Token(type=tok.type, value=tok.value, line=line, column=col,
                                offset=tok.lexpos, length=len(tok.value),
                                malformed=bool(getattr(tok, "malformed", False))))
        return tokens
