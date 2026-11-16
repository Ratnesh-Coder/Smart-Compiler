"""Correction rules: edit distance helpers and candidate generators.

Each rule is a function ``rule(diag, ctx) -> list[Candidate]`` registered for the diagnostic
*kinds* it understands. Rules only *propose* position-based edits; the engine validates, scores
and ranks them. A candidate carries four component scores in [0, 1]:

* ``grammar``    - does the edit fit the grammar (prior; replaced by re-parse verification),
* ``context``    - how well does it fit the syntactic/AST context of the error,
* ``symbol``     - is it consistent with the symbol table (visible names, types),
* ``similarity`` - edit-distance similarity between old and new text.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from compiler.ast_nodes import ProgramNode
from compiler.error_manager import Diagnostic, TextEdit
from compiler.symbol_table import SymbolTable
from compiler.tokens import KEYWORDS, TYPE_KEYWORDS, Token
from utils.source_location import LineIndex

BUILTIN_FUNCTIONS = ("printf", "scanf")


# =========================================================================== edit distance
def levenshtein(a: str, b: str) -> int:
    """Classic Levenshtein edit distance (insert / delete / substitute)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def damerau_levenshtein(a: str, b: str) -> int:
    """Optimal-string-alignment distance: Levenshtein plus adjacent transposition (``itn`` -> ``int`` = 1)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def similarity(a: str, b: str) -> float:
    """Normalised similarity in [0, 1] derived from the transposition-aware distance."""
    longest = max(len(a), len(b)) or 1
    return max(0.0, 1.0 - damerau_levenshtein(a, b) / longest)


def max_typo_distance(word: str) -> int:
    return 1 if len(word) <= 4 else 2


def closest_matches(word: str, candidates: list[str], limit: int = 3) -> list[tuple[str, int]]:
    """Candidates within the typo threshold of ``word``, nearest first (case differences count as 1)."""
    out = []
    for c in dict.fromkeys(candidates):
        if c == word:
            continue
        d = damerau_levenshtein(word, c)
        if word.lower() == c.lower():
            d = 1
        if d <= max_typo_distance(word):
            out.append((c, d))
    out.sort(key=lambda cd: (cd[1], cd[0]))
    return out[:limit]


# =========================================================================== data structures
@dataclass
class Candidate:
    description: str
    edits: list[TextEdit]
    explanation: str
    method: str
    grammar: float = 0.5
    context: float = 0.5
    symbol: float = 0.5
    similarity: float = 0.5
    assumption: Optional[str] = None


class CorrectionContext:
    """Everything a rule may consult: source text, tokens, AST and symbol table."""

    def __init__(self, source: str, tokens: list[Token], ast: Optional[ProgramNode] = None,
                 symbols: Optional[SymbolTable] = None, can_verify: bool = False) -> None:
        self.source = source
        self.tokens = tokens
        self.ast = ast
        self.symbols = symbols
        self.can_verify = can_verify
        self.index = LineIndex(source)
        self._offsets = [t.offset for t in tokens]
        self._declared: Optional[set[str]] = None

    # ---- token navigation
    def token_index_at(self, offset: int) -> int:
        i = bisect.bisect_right(self._offsets, offset) - 1
        if i >= 0 and self.tokens[i].offset <= offset < self.tokens[i].end_offset:
            return i
        if 0 <= i + 1 < len(self.tokens) and self.tokens[i + 1].offset == offset:
            return i + 1
        return -1

    def token_at(self, offset: int) -> Optional[Token]:
        i = self.token_index_at(offset)
        return self.tokens[i] if i >= 0 else None

    def tokens_on_line(self, line: int) -> list[tuple[int, Token]]:
        return [(i, t) for i, t in enumerate(self.tokens) if t.line == line]

    def semicolon_after(self, offset: int) -> Optional[Token]:
        """First ';' at or after ``offset`` that is not nested inside parentheses."""
        depth = 0
        for t in self.tokens[max(self.token_index_at(offset), 0):]:
            if t.offset < offset:
                continue
            if t.type in ("LPAREN", "LBRACKET"):
                depth += 1
            elif t.type in ("RPAREN", "RBRACKET"):
                depth -= 1
            elif t.type == "SEMI" and depth <= 0:
                return t
        return None

    def declared_names(self) -> set[str]:
        """Identifiers declared anywhere (variables, parameters, functions): ID directly after a type keyword."""
        if self._declared is None:
            names: set[str] = set(BUILTIN_FUNCTIONS)
            for i, t in enumerate(self.tokens[:-1]):
                nxt = self.tokens[i + 1]
                if t.value in TYPE_KEYWORDS and nxt.type == "ID":
                    names.add(nxt.value)
            for i, t in enumerate(self.tokens[:-1]):  # later names of "int a, b, c;"
                if t.type == "COMMA" and self.tokens[i + 1].type == "ID":
                    names.add(self.tokens[i + 1].value)
            self._declared = names
        return self._declared

    # ---- text helpers
    def body_indent(self, brace_offset: int) -> str:
        """Indentation to use for a statement inserted right after the ``{`` at ``brace_offset``."""
        line, _ = self.index.locate(brace_offset)
        for ln in range(line + 1, min(line + 6, self.index.line_count + 1)):
            if self.index.line_text(ln).strip():
                ind = self.index.indent_of(ln)
                if ind:
                    return ind
                break
        return self.index.indent_of(line) + "    "

    def removal_edit(self, start: int, end: int) -> TextEdit:
        """Delete ``[start, end)``; if that leaves a blank line, remove the whole line."""
        line, _ = self.index.locate(start)
        ls, le = self.index.line_start(line), self.index.line_end(line)
        if end <= le and not (self.source[ls:start] + self.source[end:le]).strip():
            return TextEdit(ls, min(le + 1, len(self.source)), "")
        return TextEdit(start, end, "")


RuleFunc = Callable[[Diagnostic, CorrectionContext], list[Candidate]]
RULES: dict[str, list[RuleFunc]] = {}
GENERIC_RULES: list[tuple[set[str], RuleFunc]] = []


def rule(*kinds: str) -> Callable[[RuleFunc], RuleFunc]:
    def deco(fn: RuleFunc) -> RuleFunc:
        for k in kinds:
            RULES.setdefault(k, []).append(fn)
        return fn
    return deco


# =========================================================================== lexical rules
_SMART_CHARS = {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2013": "-",
                "\u2014": "-", "\u00d7": "*", "\u00f7": "/", "\u2264": "<=", "\u2265": ">=", "\u2260": "!="}


@rule("invalid_character")
def rule_invalid_character(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    ch = d.context.get("char", "")
    out: list[Candidate] = []
    if ch in _SMART_CHARS:
        new = _SMART_CHARS[ch]
        out.append(Candidate(f"Replace the typographic character with '{new}'", [TextEdit(d.offset, d.offset + 1, new)],
                             "Characters such as curly quotes usually come from copying text out of a word "
                             "processor; C needs the plain ASCII character.", "rule", 0.9, 0.9, 0.5, 0.9))
    what = f"'{ch}'" if ch.isprintable() else "it"
    out.append(Candidate(f"Remove the invalid character {what}", [ctx.removal_edit(d.offset, d.offset + 1)
                                                                   if not ch.strip() else TextEdit(d.offset, d.offset + 1, "")],
                         "The character is not valid C; if it was typed by accident it can simply be deleted.",
                         "rule", 0.9, 0.6, 0.5, 0.9))
    return out


@rule("malformed_number")
def rule_malformed_number(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    lexeme, prefix = d.context.get("lexeme", ""), d.context.get("valid_prefix", "")
    out: list[Candidate] = []
    if prefix:
        out.append(Candidate(f"Use the valid number '{prefix}'", [TextEdit(d.offset, d.offset + len(lexeme), prefix)],
                             f"'{prefix}' is the longest valid numeric prefix of '{lexeme}'; the remaining characters are "
                             "not allowed in a number.", "rule", 0.8, 0.6, 0.5, similarity(lexeme, prefix)))
    if re.match(r"^\d+[A-Za-z_]", lexeme) and "." not in lexeme:
        new = "_" + lexeme
        out.append(Candidate(f"Rename to the identifier '{new}'", [TextEdit(d.offset, d.offset + len(lexeme), new)],
                             "Identifiers cannot start with a digit; a leading underscore makes the name legal.",
                             "rule", 0.7, 0.4, 0.4, similarity(lexeme, new),
                             assumption="Assumes the token was meant to be a variable name; other uses of the name must be renamed too."))
    return out


@rule("unterminated_string")
def rule_unterminated_string(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    lexeme = d.context.get("lexeme", "")
    body = lexeme.rstrip(" \t);,]}")
    pos = d.offset + len(body)
    return [Candidate('Insert the missing closing quote (")', [TextEdit(pos, pos, '"')],
                      "A string literal must end with a double quote on the same line; the quote is placed before the "
                      "trailing ')' / ';' of the statement.", "rule", 0.85, 0.85, 0.5, 1.0)]


@rule("unterminated_comment")
def rule_unterminated_comment(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    line_end = ctx.index.line_end(d.line)
    return [
        Candidate("Close the comment at the end of its first line", [TextEdit(line_end, line_end, " */")],
                  "Most block comments are a single line; closing it there lets the following code compile.",
                  "rule", 0.8, 0.7, 0.5, 1.0),
        Candidate("Close the comment at the end of the file", [TextEdit(len(ctx.source), len(ctx.source), "\n*/")],
                  "Keeps the whole commented text inside the comment.", "rule", 0.6, 0.4, 0.5, 1.0),
    ]


@rule("invalid_char_literal")
def rule_invalid_char_literal(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    lexeme, reason = d.context.get("lexeme", ""), d.context.get("reason", "")
    end = d.offset + len(lexeme)
    if reason == "unterminated":
        return [Candidate("Insert the missing closing quote (')", [TextEdit(end, end, "'")],
                          "A character literal is one character between single quotes.", "rule", 0.8, 0.8, 0.5, 1.0)]
    if reason == "multi":
        inner = lexeme[1:-1]
        return [Candidate(f'Use a string literal "{inner}"', [TextEdit(d.offset, end, f'"{inner}"')],
                          "Several characters need double quotes (a string).", "rule", 0.8, 0.6, 0.5, 0.8),
                Candidate(f"Keep only the first character '{inner[:1]}'", [TextEdit(d.offset, end, f"'{inner[:1]}'")],
                          "A char holds exactly one character.", "rule", 0.7, 0.4, 0.5, 0.6,
                          assumption="Assumes only the first character matters.")]
    return [Candidate("Use the null character '\\0'", [TextEdit(d.offset, end, "'\\0'")],
                      "An empty character literal is not valid; '\\0' is the usual 'empty' char.", "rule", 0.6, 0.4, 0.5, 0.5,
                      assumption="Assumes you wanted the terminating null character.")]


# =========================================================================== syntax: missing tokens
@rule("missing_semicolon", "missing_rparen", "missing_rbracket", "missing_brace")
def rule_insert_missing_token(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    pos, text = d.context.get("insert_offset"), d.context.get("insert_text")
    if pos is None or not text:
        return []
    tt, count = d.context.get("repair_token"), int(d.context.get("missing_count", 1))
    if tt == "RBRACE":
        text = "\n}" * count
    elif tt == "LBRACE":
        text = " {"
    tok_idx = ctx.token_index_at(d.context.get("token_offset", -1))
    on_new_line = tok_idx >= 0 and ctx.tokens[tok_idx].line > d.context.get("prev_line", 0)
    context_score = 0.9 if (on_new_line or tt in ("RPAREN", "RBRACKET", "RBRACE", "LBRACE")) else 0.7
    names = {"SEMI": "';'", "RPAREN": "')'", "RBRACKET": "']'", "RBRACE": "'}'", "LBRACE": "'{'"}
    where = "at the end of the file" if d.context.get("token_type") == "$end" else \
        f"after {d.context.get('previous', 'the previous token')}"
    return [Candidate(f"Insert {names.get(tt, text)} {where}", [TextEdit(pos, pos, text)],
                      d.description, "grammar", 0.9, context_score, 0.5, 1.0)]


_PUNCT_NAMES = {"SEMI": ";", "COMMA": ",", "RPAREN": ")", "RBRACKET": "]"}


def grammar_repair(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    """Grammar-based insertions/deletions derived from the tokens the parser could accept.

    These are speculative, so they are only proposed when the engine can verify them by re-parsing.
    """
    if not ctx.can_verify:
        return []
    tok_off = d.context.get("token_offset", d.offset)
    prev_end = d.context.get("prev_end", -1)
    tok = ctx.token_at(tok_off) if tok_off >= 0 else None
    expected = [e for e in d.context.get("expected", []) if e in _PUNCT_NAMES]
    out: list[Candidate] = []
    for e in expected:
        pos = prev_end if (e in ("SEMI", "COMMA") and prev_end >= 0) else (tok.offset if tok else len(ctx.source))
        out.append(Candidate(f"Insert '{_PUNCT_NAMES[e]}'", [TextEdit(pos, pos, _PUNCT_NAMES[e])],
                             f"The grammar allows '{_PUNCT_NAMES[e]}' at this position.", "grammar", 0.5, 0.4, 0.5, 1.0))
    if tok is not None:
        out.append(Candidate(f"Delete the unexpected {d.context.get('token', 'token')}",
                             [TextEdit(tok.offset, tok.end_offset, "")],
                             "Removing the token that does not fit the grammar may leave a valid statement.",
                             "grammar", 0.5, 0.35, 0.5, 0.6))
    if d.kind == "missing_operand" and tok is not None:
        out.append(Candidate("Insert a placeholder operand '0'", [TextEdit(tok.offset, tok.offset, "0")],
                             "An operand is missing; '0' makes the expression well-formed.", "grammar", 0.5, 0.3, 0.5, 1.0,
                             assumption="'0' is only a placeholder - replace it with the value you intended."))
    return out


GENERIC_RULES.append(({"unexpected_token", "missing_operand", "unexpected_eof"}, grammar_repair))

_REVERSED_OPS = {"=<": "<=", "=>": ">=", "<>": "!=", "=!": "!="}


def reversed_operator(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    """Detect operators typed in the wrong order, e.g. ``=<`` instead of ``<=``."""
    tok = ctx.token_at(d.context.get("token_offset", d.offset))
    prev = ctx.token_at(d.context.get("prev_offset", -1)) if d.context.get("prev_offset", -1) >= 0 else None
    if tok is None or prev is None or prev.end_offset != tok.offset:
        return []
    joined = prev.value + tok.value
    if joined not in _REVERSED_OPS:
        return []
    new = _REVERSED_OPS[joined]
    return [Candidate(f"Write the operator as '{new}'", [TextEdit(prev.offset, tok.end_offset, new)],
                      f"'{joined}' is not a C operator; the comparison operator is written '{new}'.",
                      "rule", 0.95, 0.95, 0.5, 1.0)]


GENERIC_RULES.append(({"unexpected_token", "missing_operand"}, reversed_operator))


# =========================================================================== keyword typos
def _keyword_context(keyword: str, tokens: list[Token], i: int) -> float:
    prev = tokens[i - 1] if i > 0 else None
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    at_stmt_start = prev is None or prev.type in ("SEMI", "LBRACE", "RBRACE", "RPAREN")
    if keyword in TYPE_KEYWORDS:
        return 1.0 if nxt is not None and nxt.type == "ID" else 0.3
    if keyword in ("if", "while", "for"):
        return 1.0 if at_stmt_start and nxt is not None and nxt.type == "LPAREN" else 0.3
    if keyword == "else":
        return 1.0 if prev is not None and prev.type in ("RBRACE", "SEMI") else 0.3
    if keyword == "do":
        return 1.0 if at_stmt_start and nxt is not None and nxt.type == "LBRACE" else 0.3
    if keyword in ("return", "break", "continue"):
        return 1.0 if at_stmt_start else 0.3
    return 0.3


def keyword_typo(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    """Misspelt keywords: unknown identifiers close (edit distance) to a C keyword."""
    declared = ctx.declared_names()
    lines = {d.line, d.context.get("prev_line", d.line)}
    out: list[Candidate] = []
    for line in lines:
        for i, t in ctx.tokens_on_line(line):
            if t.type != "ID" or t.value in declared:
                continue
            for kw in KEYWORDS:
                dist = damerau_levenshtein(t.value, kw)
                if len(t.value) < 2 or dist > max_typo_distance(kw) or dist == 0:
                    continue
                if len(kw) <= 3 and dist > 1:
                    continue
                score = _keyword_context(kw, ctx.tokens, i)
                out.append(Candidate(f"Replace '{t.value}' with the keyword '{kw}'",
                                     [TextEdit(t.offset, t.end_offset, kw)],
                                     f"'{t.value}' is not declared anywhere and differs from the keyword '{kw}' by "
                                     f"{dist} edit(s) (Damerau-Levenshtein distance).",
                                     "edit_distance", 0.9, score, 0.5, similarity(t.value, kw)))
    return out


GENERIC_RULES.append(({"unexpected_token", "missing_semicolon", "missing_operand", "missing_rparen",
                       "undeclared_identifier", "undeclared_function", "unexpected_eof", "missing_brace"},
                      keyword_typo))


# =========================================================================== semantic: names
def _declare_candidate(d: Diagnostic, ctx: CorrectionContext) -> Optional[Candidate]:
    name = d.context.get("name", "")
    typ = d.context.get("inferred_type", "int")
    typ = typ if typ in ("int", "float", "double", "char") else "int"
    body = d.context.get("function_body_offset", -1)
    text = f"{typ} {name};"
    if body >= 0:
        edit = TextEdit(body + 1, body + 1, "\n" + ctx.body_indent(body) + text)
        where = "at the start of the enclosing function"
    else:
        edit = TextEdit(0, 0, text + "\n")
        where = "at the top of the file"
    return Candidate(f"Declare '{typ} {name};' {where}", [edit],
                     f"'{name}' is used without a declaration; declaring it makes the program well-formed.",
                     "symbol_table", 0.9, 0.6, 0.5, 1.0,
                     assumption=f"Assumes '{name}' is a new variable of type '{typ}' (type inferred from how it is used).")


@rule("undeclared_identifier", "scope_violation")
def rule_undeclared_identifier(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    name = d.context.get("name", "")
    visible = d.context.get("visible", [])
    usage = d.context.get("usage", "read")
    pool = [v["name"] for v in visible if v["category"] != "function"]
    out: list[Candidate] = []
    for cand, dist in closest_matches(name, pool):
        sym = next(v for v in visible if v["name"] == cand)
        out.append(Candidate(f"Replace '{name}' with the declared variable '{cand}' ({sym['type']})",
                             [TextEdit(d.offset, d.offset + len(name), cand)],
                             f"'{cand}' is visible here and is {dist} edit(s) away from '{name}'; it is probably a typo.",
                             "edit_distance", 0.9, 1.0 if usage in ("read", "write") else 0.5, 1.0, similarity(name, cand)))
    declare = _declare_candidate(d, ctx)
    if declare:
        if d.kind == "scope_violation":
            declare.explanation = (f"'{name}' was declared inside a block that has ended (line "
                                   f"{d.context.get('declared_line')}); declaring it at function level makes it visible "
                                   "everywhere in the function.")
            declare.context = 0.5
        out.append(declare)
    return out


@rule("undeclared_function")
def rule_undeclared_function(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    name = d.context.get("name", "")
    funcs = [f["name"] for f in d.context.get("functions", [])]
    out: list[Candidate] = []
    for cand, dist in closest_matches(name, funcs):
        f = next(x for x in d.context["functions"] if x["name"] == cand)
        arity_ok = f["params"] is None or len(f["params"]) == d.context.get("arg_count", 0) or cand in BUILTIN_FUNCTIONS
        out.append(Candidate(f"Replace '{name}' with the function '{cand}'",
                             [TextEdit(d.offset, d.offset + len(name), cand)],
                             f"'{cand}' is a declared function {dist} edit(s) away from '{name}'.",
                             "edit_distance", 0.9, 1.0 if arity_ok else 0.6, 1.0, similarity(name, cand)))
    types = d.context.get("arg_types", [])
    params = []
    for i, t in enumerate(types):
        if t in ("int", "float", "double", "char"):
            params.append(f"{t} a{i}")
        elif t.endswith("[]"):
            params.append(f"{t[:-2]} a{i}[]")
        else:
            params = None
            break
    if params is not None:
        proto = f"int {name}({', '.join(params)});"
        out.append(Candidate(f"Add the prototype '{proto}' at the top of the file", [TextEdit(0, 0, proto + "\n")],
                             "A function must be declared before it is called.", "symbol_table", 0.8, 0.4, 0.4, 0.5,
                             assumption="Assumes the function returns int and exists elsewhere (you must still define it)."))
    return out


# =========================================================================== semantic: types
def _change_decl_type(d: Diagnostic, new_type: str, why: str) -> Optional[Candidate]:
    off, ln = d.context.get("target_type_offset", -1), d.context.get("target_type_length", 0)
    name = d.context.get("target_name")
    if off < 0 or not ln or not name:
        return None
    return Candidate(f"Change the type of '{name}' to '{new_type}'", [TextEdit(off, off + ln, new_type)], why,
                     "symbol_table", 0.8, 0.6, 0.8, 0.5,
                     assumption=f"Changes the type of '{name}' everywhere it is used, which can alter results.")


@rule("type_mismatch")
def rule_type_mismatch(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    out: list[Candidate] = []
    name = d.context.get("target_name")
    off, ln = d.context.get("target_type_offset", -1), d.context.get("target_type_length", 0)
    name_off = d.context.get("target_name_offset", -1)
    if d.context.get("source_is_string") and name and off >= 0 and name_off >= 0 \
            and d.context.get("target_category") == "variable" and d.context.get("where") == "declaration":
        out.append(Candidate(f"Declare '{name}' as a char array so it can hold the string",
                             [TextEdit(off, off + ln, "char"), TextEdit(name_off + len(name), name_off + len(name), "[]")],
                             "A string is an array of characters, so the variable must be a 'char' array.",
                             "symbol_table", 0.85, 0.8, 0.9, 0.5,
                             assumption=f"Assumes '{name}' is meant to hold text; later numeric uses of it will need changes."))
    elif d.context.get("source_is_string") and name and d.context.get("target_category") == "variable":
        c = _change_decl_type(d, "char", "Strings are stored in 'char' arrays; declare the variable as a char array "
                                         "(e.g. 'char name[20];') and use strcpy-style copying.")
        if c:  # type alone is insufficient for an assignment, keep it advisory
            c.edits = []
            out.append(c)
    return out


@rule("implicit_conversion")
def rule_implicit_conversion(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    src = d.context.get("source_type")
    if src not in ("float", "double"):
        return []
    if d.context.get("change_func_type"):
        off, ln = d.context.get("func_type_offset", -1), d.context.get("func_type_length", 0)
        if off >= 0 and ln:
            return [Candidate(f"Change the function's return type to '{src}'", [TextEdit(off, off + ln, src)],
                              "The returned value is floating point; the function should return the same type.",
                              "symbol_table", 0.8, 0.7, 0.8, 0.5,
                              assumption="Callers that expect the old return type will now receive a different type.")]
        return []
    c = _change_decl_type(d, src, f"The value has type '{src}'; declaring the target with the same type avoids "
                                  "losing the fractional part.")
    return [c] if c else []


# =========================================================================== semantic: calls & returns
def _argument_spans(ctx: CorrectionContext, lparen: int, rparen: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    depth, start, last_end = 0, None, None
    for t in ctx.tokens:
        if t.offset <= lparen or t.offset >= rparen:
            continue
        if t.type in ("LPAREN", "LBRACKET"):
            depth += 1
        elif t.type in ("RPAREN", "RBRACKET"):
            depth -= 1
        if t.type == "COMMA" and depth == 0:
            if start is not None:
                spans.append((start, last_end))  # type: ignore[arg-type]
            start = None
            continue
        if start is None:
            start = t.offset
        last_end = t.end_offset
    if start is not None:
        spans.append((start, last_end))  # type: ignore[arg-type]
    return spans


_DEFAULTS = {"int": "0", "char": "0", "float": "0.0", "double": "0.0"}


@rule("wrong_argument_count")
def rule_argument_count(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    lp, rp = d.context.get("lparen_offset", -1), d.context.get("rparen_offset", -1)
    exp, got = d.context.get("expected", 0), d.context.get("got", 0)
    if lp < 0 or rp < 0:
        return []
    spans = _argument_spans(ctx, lp, rp)
    fn = d.context.get("function", "function")
    if len(spans) != got:
        return []
    if got > exp:
        start = spans[exp - 1][1] if exp > 0 else lp + 1
        extra = got - exp
        return [Candidate(f"Remove the {extra} extra argument(s) from the call to '{fn}'",
                          [TextEdit(start, spans[-1][1], "")],
                          f"'{fn}' declares {exp} parameter(s); the surplus arguments are dropped from the end.",
                          "symbol_table", 0.8, 0.7, 0.9, 0.7,
                          assumption="The last arguments are assumed to be the surplus ones; check which values you really need.")]
    ptypes = d.context.get("param_types", [])[got:]
    vals = []
    for t in ptypes:
        if t.endswith("[]") or t not in _DEFAULTS:
            return []
        vals.append(_DEFAULTS[t])
    text = (", " if got else "") + ", ".join(vals)
    return [Candidate(f"Add {len(vals)} missing argument(s) to the call to '{fn}'", [TextEdit(rp, rp, text)],
                      f"'{fn}' needs {exp} argument(s); default placeholder values are appended.",
                      "symbol_table", 0.8, 0.6, 0.9, 0.7,
                      assumption="Placeholder values (0 / 0.0) are inserted; replace them with the real arguments.")]


@rule("wrong_argument_type")
def rule_argument_type(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    if d.context.get("missing_address_of"):
        off = d.context.get("arg_offset", -1)
        if off >= 0:
            return [Candidate("Insert '&' before the variable", [TextEdit(off, off, "&")],
                              "scanf writes into the variable through its address, so the argument must be '&variable'.",
                              "rule", 0.95, 0.95, 0.9, 1.0)]
    return []


@rule("invalid_return")
def rule_invalid_return(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    reason, ret_off = d.context.get("reason"), d.context.get("return_offset", -1)
    fn = d.context.get("function", "")
    out: list[Candidate] = []
    if ret_off < 0:
        return out
    if reason == "void_returns_value":
        semi = ctx.semicolon_after(ret_off)
        if semi is not None:
            out.append(Candidate("Remove the returned value (keep 'return;')",
                                 [TextEdit(ret_off + len("return"), semi.offset, "")],
                                 f"'{fn}' is void, so 'return' must not carry a value.", "ast", 0.9, 0.8, 0.8, 0.6))
        got, off, ln = d.context.get("got"), d.context.get("func_type_offset", -1), d.context.get("func_type_length", 0)
        if got in ("int", "float", "double", "char") and off >= 0:
            out.append(Candidate(f"Change the return type of '{fn}' to '{got}'", [TextEdit(off, off + ln, got)],
                                 "If the function should produce a value, its return type must not be void.",
                                 "ast", 0.8, 0.5, 0.7, 0.4,
                                 assumption="Callers of this function will now receive a value; check they handle it."))
    elif reason == "missing_value":
        exp = d.context.get("expected", "int")
        out.append(Candidate(f"Return a value ('return {_DEFAULTS.get(exp, '0')};')",
                             [TextEdit(ret_off + len("return"), ret_off + len("return"), f" {_DEFAULTS.get(exp, '0')}")],
                             f"'{fn}' must return a '{exp}'.", "ast", 0.85, 0.7, 0.8, 0.6,
                             assumption="Returns a default value; use the value your function really computes."))
    return out


@rule("missing_return")
def rule_missing_return(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    end = d.context.get("body_end_offset", -1)
    if end < 0:
        return []
    value = _DEFAULTS.get(d.context.get("return_type", "int"), "0")
    line, col = ctx.index.locate(end)
    if not ctx.index.line_text(line)[: col - 1].strip():
        edit = TextEdit(ctx.index.line_start(line), ctx.index.line_start(line),
                        ctx.index.indent_of(line) + "    " + f"return {value};\n")
    else:
        edit = TextEdit(end, end, f"return {value}; ")
    return [Candidate(f"Add 'return {value};' before the closing brace", [edit],
                      "Every path of a non-void function should end with a return statement.", "ast", 0.9, 0.7, 0.8, 1.0,
                      assumption=f"Returns the default value {value}; replace it with the value your function should return.")]


@rule("assignment_in_condition")
def rule_assignment_in_condition(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    off, ln = d.context.get("op_offset", -1), d.context.get("op_length", 1)
    if off < 0:
        return []
    return [Candidate("Replace '=' with '==' to compare instead of assign", [TextEdit(off, off + ln, "==")],
                      "Inside a condition a comparison is almost always intended.", "rule", 0.9, 0.9, 0.5, 0.9,
                      assumption="Assumes you meant to compare; if you really wanted to assign, keep the code and add parentheses.")]


@rule("break_continue_outside_loop")
def rule_break_outside(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    off = d.context.get("stmt_offset", -1)
    semi = ctx.semicolon_after(off) if off >= 0 else None
    if semi is None:
        return []
    word = d.context.get("keyword", "break")
    return [Candidate(f"Remove the '{word}' statement", [ctx.removal_edit(off, semi.end_offset)],
                      f"'{word}' has no loop to act on here.", "ast", 0.7, 0.5, 0.5, 0.5,
                      assumption="Deleting the statement changes control flow.")]


@rule("duplicate_declaration")
def rule_duplicate_declaration(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    name = d.context.get("name", "")
    t_off, n_off = d.context.get("type_offset", -1), d.context.get("name_offset", d.offset)
    out: list[Candidate] = []
    single = d.context.get("declarator_count", 1) == 1
    if t_off >= 0 and single and d.context.get("has_initializer"):
        out.append(Candidate(f"Turn the redeclaration into an assignment to the existing '{name}'",
                             [TextEdit(t_off, n_off, "")],
                             "Dropping the type keyword assigns the value to the variable declared earlier.",
                             "symbol_table", 0.85, 0.8, 0.9, 0.7,
                             assumption=f"Assumes both declarations should refer to the same variable '{name}'."))
    elif t_off >= 0 and single:
        semi = ctx.semicolon_after(n_off)
        if semi is not None:
            out.append(Candidate(f"Remove the redundant declaration of '{name}'",
                                 [ctx.removal_edit(d.context.get("decl_offset", t_off), semi.end_offset)],
                                 "The variable already exists in this scope, so the second declaration is unnecessary.",
                                 "symbol_table", 0.85, 0.8, 0.9, 0.7))
    declared = ctx.declared_names()
    k = 2
    while f"{name}{k}" in declared:
        k += 1
    new = f"{name}{k}"
    out.append(Candidate(f"Rename the second variable to '{new}'", [TextEdit(n_off, n_off + len(name), new)],
                         "A different name makes the declaration legal.", "symbol_table", 0.8, 0.5, 0.6,
                         similarity(name, new),
                         assumption=f"Later uses of the second variable must also be renamed to '{new}'."))
    return out


@rule("invalid_array_access")
def rule_array_access(d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
    if d.context.get("reason") != "out_of_bounds":
        return []
    size, idx = d.context.get("size", 0), d.context.get("index", 0)
    tok = ctx.token_at(d.offset)
    if tok is None or tok.type != "INT_CONST":
        return []
    last = size - 1 if idx >= size else 0
    return [Candidate(f"Use the valid index {last}", [TextEdit(tok.offset, tok.end_offset, str(last))],
                      f"Valid indices for an array of size {size} are 0..{size - 1}.", "ast", 0.7, 0.5, 0.8,
                      similarity(tok.value, str(last)),
                      assumption="Picks the nearest valid index; verify it is the element you want.")]
