"""Lexical analysis tests (require PLY)."""
import pytest

pytest.importorskip("ply")

from compiler.error_manager import ErrorManager  # noqa: E402
from compiler.lexer import CLexer  # noqa: E402
from tests.helpers import sample  # noqa: E402


def lex(src):
    em = ErrorManager(src)
    return CLexer(em).tokenize(src), em


def types(src):
    return [t.type for t in lex(src)[0]]


def test_declaration_tokens():
    assert types("int x = 10;") == ["INT", "ID", "ASSIGN", "INT_CONST", "SEMI"]


def test_all_keywords_recognised_and_not_confused_with_identifiers():
    src = "int float double char void if else for while do break continue return"
    assert types(src) == [w.upper() for w in src.split()]
    assert types("integer returns iff") == ["ID", "ID", "ID"]


def test_operator_longest_match():
    assert types("<= >= == != && || ++ -- += -= *= /= %=") == [
        "LE", "GE", "EQ", "NE", "LAND", "LOR", "INC", "DEC", "PLUS_ASSIGN", "MINUS_ASSIGN",
        "TIMES_ASSIGN", "DIVIDE_ASSIGN", "MOD_ASSIGN"]
    assert types("a<b>c=d!e&f+g-h*i/j%k") == [
        "ID", "LT", "ID", "GT", "ID", "ASSIGN", "ID", "LNOT", "ID", "AMP", "ID", "PLUS", "ID", "MINUS",
        "ID", "TIMES", "ID", "DIVIDE", "ID", "MOD", "ID"]


def test_delimiters():
    assert types("( ) { } [ ] ; ,") == ["LPAREN", "RPAREN", "LBRACE", "RBRACE", "LBRACKET", "RBRACKET", "SEMI", "COMMA"]


def test_valid_numeric_literals():
    toks, em = lex("0 42 3.14 .5 1e10 2.5e-3 0x1F 10u 1.5f 5.")
    assert len(em) == 0
    assert [t.type for t in toks] == ["INT_CONST", "INT_CONST", "FLOAT_CONST", "FLOAT_CONST", "FLOAT_CONST",
                                      "FLOAT_CONST", "INT_CONST", "INT_CONST", "FLOAT_CONST", "FLOAT_CONST"]


def test_string_and_char_literals():
    src = '"hello \\"x\\" world" \'a\' \'\\n\' "%d\\n"'
    toks, em = lex(src)
    assert len(em) == 0
    assert [t.type for t in toks] == ["STRING_LITERAL", "CHAR_CONST", "CHAR_CONST", "STRING_LITERAL"]
    assert toks[0].value == r'"hello \"x\" world"'


def test_comments_are_ignored_and_line_numbers_stay_correct():
    toks, em = lex("// line comment\nint a; /* block\ncomment */ int b;\n/* x */int c;")
    assert len(em) == 0
    assert [(t.value, t.line, t.column) for t in toks if t.type == "ID"] == [("a", 2, 5), ("b", 3, 16), ("c", 4, 12)]


def test_line_and_column_are_accurate():
    toks, _ = lex("int x;\n  y = 2;\n\tz;")
    by_val = {t.value: (t.line, t.column) for t in toks}
    assert by_val["x"] == (1, 5) and by_val["y"] == (2, 3) and by_val["2"] == (2, 7)
    assert by_val["z"] == (3, 2)  # a tab counts as one column


def test_token_offsets_slice_back_to_the_lexeme():
    src = sample("valid_program.c")
    toks, em = lex(src)
    assert em.error_count() == 0 and len(toks) > 100
    for t in toks:
        assert src[t.offset:t.end_offset] == t.value
        assert t.line >= 1 and t.column >= 1


def test_spec_example_invalid_character():
    toks, em = lex("int @number = 10;")
    (d,) = em.all()
    assert d.error_id == "LEX001" and d.category == "Lexical"
    assert d.message == "Invalid character '@'"
    assert (d.line, d.column) == (1, 5)
    assert "Remove the invalid character" in d.suggestion
    assert [t.type for t in toks] == ["INT", "ID", "ASSIGN", "INT_CONST", "SEMI"]  # '@' skipped but reported


def test_every_invalid_character_is_reported_never_discarded():
    _, em = lex("a @ b $ c ` d")
    assert [d.context["char"] for d in em.all()] == ["@", "$", "`"]


@pytest.mark.parametrize("text", ["12abc", "1.2.3", "1e", "0x", "3..4", "9z9"])
def test_malformed_numbers(text):
    toks, em = lex(f"x = {text};")
    (d,) = em.all()
    assert d.error_id == "LEX002" and text in d.message
    assert d.offset == 4 and d.length == len(text)
    assert toks[2].malformed and toks[2].value == text  # still emitted so the parser can continue


def test_unterminated_string():
    toks, em = lex('printf("Hello world);\nint x;')
    (d,) = em.all()
    assert d.error_id == "LEX003" and (d.line, d.column) == (1, 8)
    assert toks[2].type == "STRING_LITERAL" and toks[2].malformed
    assert [t.type for t in toks[-3:]] == ["INT", "ID", "SEMI"]  # lexing recovers on the next line


def test_unterminated_block_comment():
    toks, em = lex("int a;\n/* never closed\nint b;")
    (d,) = em.all()
    assert d.error_id == "LEX004" and (d.line, d.column) == (2, 1)
    assert [t.type for t in toks] == ["INT", "ID", "SEMI"]


@pytest.mark.parametrize("text,reason", [("''", "empty"), ("'ab'", "multi"), ("'a", "unterminated")])
def test_invalid_char_literals(text, reason):
    _, em = lex(f"char c = {text};")
    (d,) = [x for x in em.all() if x.error_id == "LEX005"]
    assert d.context["reason"] == reason


def test_preprocessor_lines_are_ignored_with_an_informational_note():
    toks, em = lex("#include <stdio.h>\nint x;")
    assert em.error_count() == 0
    assert [d.severity for d in em.all()] == ["suggestion"] and em.all()[0].error_id == "LEX006"
    assert [t.type for t in toks] == ["INT", "ID", "SEMI"]


def test_empty_and_whitespace_sources():
    assert lex("")[0] == [] and lex(" \n\t\n")[0] == []


def test_lexer_is_reusable_between_inputs():
    em = ErrorManager("")
    lx = CLexer(em)
    assert len(lx.tokenize("int a;")) == 3
    assert [t.value for t in lx.tokenize("float b;")] == ["float", "b", ";"]


def test_lexical_error_sample_file():
    _, em = lex(sample("lexical_errors.c"))
    assert sorted(d.error_id for d in em.errors()) == ["LEX001", "LEX002", "LEX002", "LEX003"]
