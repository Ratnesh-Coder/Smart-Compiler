"""Correction engine, edit distance, safe code fixer and UI controller tests (PLY independent).

Diagnostics are produced through the real ``ErrorManager`` / semantic analyzer. The controller tests
use a *stub analyzer* purely to exercise state handling (they do not claim compiler behaviour).
"""
import json

import pytest

from compiler.code_fixer import (CodeFixer, CodeFixerError, ConfirmationRequired, OverlappingEditError,
                                 StaleCorrectionError, apply_edits, edits_conflict, source_hash)
from compiler.correction_engine import CorrectionEngine, ScoringWeights, VerificationOutcome
from compiler.correction_rules import (CorrectionContext, closest_matches, damerau_levenshtein,
                                       levenshtein, similarity)
from compiler.error_manager import Correction, Diagnostic, ErrorManager, TextEdit
from ui.session import AppController


def make_diag(source, kind, offset, **context):
    em = ErrorManager(source)
    line, col = em._index.locate(offset)
    d = em.report(kind, line, col, message=kind, offset=offset, context=context)
    return d


def suggest(source, diag, **engine_kw):
    ctx = CorrectionContext(source, [])
    return CorrectionEngine(**engine_kw).suggest(diag, ctx)


def apply_first(source, corrections, index=0):
    return apply_edits(source, corrections[index].edits)


# ====================================================================== edit distance
def test_levenshtein_known_values():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3 and levenshtein("same", "same") == 0
    assert levenshtein("itn", "int") == 2


def test_damerau_counts_transposition_as_one():
    assert damerau_levenshtein("itn", "int") == 1
    assert damerau_levenshtein("whlie", "while") == 1
    assert damerau_levenshtein("retrun", "return") == 1
    assert damerau_levenshtein("abc", "abd") == 1


def test_similarity_range():
    assert similarity("abc", "abc") == 1.0
    assert 0.0 <= similarity("abc", "xyz") < 0.5
    assert similarity("whlie", "while") == pytest.approx(0.8)


def test_closest_matches_threshold_ordering_and_exact_exclusion():
    pool = ["count", "counter", "total", "cuount", "count"]
    got = closest_matches("cuont", pool)
    assert {c for c, _ in got} == {"count", "cuount"} and all(d <= 2 for _, d in got)
    assert [d for _, d in got] == sorted(d for _, d in got)  # nearest first
    assert "total" not in [c for c, _ in got] and "counter" not in [c for c, _ in got]
    assert closest_matches("count", ["count"]) == []
    assert closest_matches("Count", ["count"])[0] == ("count", 1)  # case slip counts as one edit


# ====================================================================== lexical corrections
def test_invalid_character_removal_applies_to_the_right_offset():
    src = "int @number = 10;\n"
    cs = suggest(src, make_diag(src, "invalid_character", 4, char="@"))
    assert apply_first(src, cs) == "int number = 10;\n"
    assert cs[0].explanation


def test_smart_quote_is_replaced_not_removed():
    src = "printf(\u201chi\u201d);"
    cs = suggest(src, make_diag(src, "invalid_character", 7, char="\u201c"))
    assert apply_first(src, cs).startswith('printf("hi')


def test_malformed_number_suggests_valid_prefix():
    src = "float r = 1.2.3;\n"
    cs = suggest(src, make_diag(src, "malformed_number", 10, lexeme="1.2.3", valid_prefix="1.2"))
    assert apply_first(src, cs) == "float r = 1.2;\n"


def test_unterminated_string_inserts_quote_before_closing_paren():
    src = 'printf("Hello world);\n'
    off = src.index('"')
    cs = suggest(src, make_diag(src, "unterminated_string", off, lexeme=src[off:src.index("\n")]))
    assert apply_first(src, cs) == 'printf("Hello world");\n'


def test_unterminated_comment_offers_two_alternatives():
    src = "/* note\nint x;\n"
    cs = suggest(src, make_diag(src, "unterminated_comment", 0))
    assert len(cs) == 2
    assert apply_first(src, cs, 0).startswith("/* note */\nint x;")


# ====================================================================== symbol-based corrections
def _undeclared_source():
    return "int main() {\n    int count = 0;\n    cuont = 5;\n    return 0;\n}\n"


def _undeclared_diag(src):
    return make_diag(src, "undeclared_identifier", src.index("cuont"), name="cuont", usage="write",
                     inferred_type="int", function_body_offset=src.index("{"),
                     visible=[{"name": "count", "type": "int", "category": "variable", "params": None},
                              {"name": "main", "type": "int", "category": "function", "params": []}])


def test_similar_visible_identifier_is_ranked_first():
    src = _undeclared_source()
    cs = suggest(src, _undeclared_diag(src))
    assert "count" in cs[0].description and cs[0].method == "edit_distance"
    assert apply_first(src, cs).count("count = 5;") == 1


def test_declaration_alternative_preserves_indentation_and_is_marked_with_assumption():
    src = _undeclared_source()
    cs = suggest(src, _undeclared_diag(src))
    decl = next(c for c in cs if c.method == "symbol_table")
    assert decl.requires_confirmation and "int cuont;" in decl.description
    new = apply_edits(src, decl.edits)
    assert "{\n    int cuont;\n    int count = 0;" in new  # 4-space indent copied from the body


def test_unavailable_identifiers_are_never_suggested():
    src = _undeclared_source()
    d = make_diag(src, "undeclared_identifier", src.index("cuont"), name="cuont", usage="read",
                  inferred_type="int", function_body_offset=src.index("{"),
                  visible=[{"name": "main", "type": "int", "category": "function", "params": []}])
    assert all("count" not in c.description for c in suggest(src, d))


def test_multiple_ranked_alternatives_with_scores_and_explanations():
    src = _undeclared_source()
    cs = suggest(src, _undeclared_diag(src))
    assert len(cs) >= 2
    assert [c.confidence for c in cs] == sorted((c.confidence for c in cs), reverse=True)
    for c in cs:
        assert 0.0 < c.confidence < 1.0 and c.explanation
        assert set(c.score_breakdown) == {"grammar", "context", "symbol", "similarity"}


def test_ranking_weights_are_configurable():
    src = _undeclared_source()
    default = suggest(src, _undeclared_diag(src))
    assert default[0].method == "edit_distance"
    sim_only = suggest(src, _undeclared_diag(src), weights=ScoringWeights(0, 0, 0, 1.0))
    assert sim_only[0].method == "symbol_table"  # the declaration has similarity 1.0, the typo fix 0.8
    assert sim_only[0].confidence != default[0].confidence


def test_undeclared_function_suggests_builtin_name():
    src = 'int main() {\n    pritnf("x");\n}\n'
    d = make_diag(src, "undeclared_function", src.index("pritnf"), name="pritnf", arg_count=1, arg_types=["char[]"],
                  functions=[{"name": "printf", "type": "int", "category": "function", "params": ["char[]"]}],
                  visible=[])
    cs = suggest(src, d)
    assert "printf" in cs[0].description and apply_first(src, cs).count("printf(") == 1


# ====================================================================== AST / context based corrections
def test_missing_return_inserted_before_closing_brace():
    src = "int f() {\n    int x = 1;\n}\n"
    d = make_diag(src, "missing_return", 4, function="f", return_type="int", body_end_offset=src.rindex("}"))
    cs = suggest(src, d)
    assert apply_first(src, cs) == "int f() {\n    int x = 1;\n    return 0;\n}\n"
    assert cs[0].requires_confirmation


def test_assignment_in_condition_replaced_by_comparison():
    src = "if (count = 10) {}\n"
    off = src.index("=")
    cs = suggest(src, make_diag(src, "assignment_in_condition", off, op_offset=off, op_length=1))
    assert apply_first(src, cs) == "if (count == 10) {}\n"


def test_duplicate_declaration_alternatives():
    src = "int main() {\n    int count = 5;\n    int count = 6;\n}\n"
    second = src.rindex("int count")
    d = make_diag(src, "duplicate_declaration", second + 4, name="count", type_offset=second,
                  name_offset=second + 4, has_initializer=True, declarator_count=1, decl_offset=second)
    cs = suggest(src, d)
    texts = [apply_edits(src, c.edits) for c in cs]
    assert any("    count = 6;" in t and t.count("int count") == 1 for t in texts)
    assert any("int count2 = 6;" in t for t in texts)


def test_invalid_candidates_are_removed():
    src = "int x;\n"
    d = make_diag(src, "invalid_character", 999, char="@")  # offset outside the text
    assert suggest(src, d) == []


def test_verifier_filters_candidates_that_do_not_resolve_the_diagnostic():
    src = _undeclared_source()
    d = _undeclared_diag(src)
    base = VerificationOutcome(1, {(d.severity, *d.signature): 1})
    rejecting = CorrectionEngine(verifier=lambda s: VerificationOutcome(1, {(d.severity, *d.signature): 1}))
    ctx = CorrectionContext(src, [])
    assert rejecting.suggest(d, ctx, base) == []
    accepting = CorrectionEngine(verifier=lambda s: VerificationOutcome(0, {}))
    got = accepting.suggest(d, ctx, base)
    assert got and all(c.score_breakdown["grammar"] == 1.0 for c in got)


def test_verifier_rejects_fix_that_adds_errors():
    src = _undeclared_source()
    d = _undeclared_diag(src)
    base = VerificationOutcome(1, {(d.severity, *d.signature): 1})
    eng = CorrectionEngine(verifier=lambda s: VerificationOutcome(3, {}))
    assert eng.suggest(d, CorrectionContext(src, []), base) == []


def test_attach_sets_suggestion_confidence_and_unique_ids():
    src = _undeclared_source()
    d = _undeclared_diag(src)
    CorrectionEngine().attach([d], CorrectionContext(src, []))
    assert d.suggestion == d.corrections[0].description and d.confidence == d.corrections[0].confidence
    assert len({c.correction_id for c in d.corrections}) == len(d.corrections) and d.corrected_code


def test_diagnostic_is_json_serialisable():
    src = _undeclared_source()
    d = _undeclared_diag(src)
    CorrectionEngine().attach([d], CorrectionContext(src, []))
    assert json.loads(json.dumps(d.to_dict()))["corrections"]


# ====================================================================== code fixer
def corr(src, start, end, text, cid="C1", assumption=None):
    return Correction(cid, f"edit {cid}", [TextEdit(start, end, text)], 0.9, assumption=assumption,
                      source_hash=source_hash(src))


def test_text_edit_validation():
    with pytest.raises(ValueError):
        TextEdit(5, 2, "")
    with pytest.raises(ValueError):
        TextEdit(-1, 0, "")
    assert TextEdit(1, 3, "XY").apply("abcdef") == "aXYdef"


def test_apply_edits_is_position_based_not_global_replace():
    src = "a = a + a;"
    assert apply_edits(src, [TextEdit(4, 5, "b")]) == "a = b + a;"


def test_apply_edits_multiple_and_same_offset_order():
    assert apply_edits("abcdef", [TextEdit(4, 5, "X"), TextEdit(1, 2, "Y")]) == "aYcdXf"
    assert apply_edits("ab", [TextEdit(1, 1, "1"), TextEdit(1, 1, "2")]) == "a12b"


def test_overlapping_edits_are_detected():
    with pytest.raises(OverlappingEditError):
        apply_edits("abcdef", [TextEdit(1, 4, "X"), TextEdit(3, 5, "Y")])
    assert edits_conflict([TextEdit(1, 4, "")], [TextEdit(3, 5, "")])
    assert edits_conflict([TextEdit(2, 2, "a")], [TextEdit(2, 2, "b")])
    assert not edits_conflict([TextEdit(0, 2, "")], [TextEdit(2, 4, "")])


def test_edit_outside_source_is_rejected():
    with pytest.raises(ValueError):
        apply_edits("abc", [TextEdit(2, 10, "")])


def test_apply_preview_and_original_preserved():
    src = "int x = 1\n"
    fx = CodeFixer(src)
    c = corr(src, 9, 9, ";")
    assert fx.preview(c) == "int x = 1;\n" and fx.current == src  # preview commits nothing
    assert fx.apply(c) == "int x = 1;\n"
    assert fx.original == src and fx.current == "int x = 1;\n" and fx.applied_count == 1


def test_undo_restores_previous_source_step_by_step():
    src = "abc"
    fx = CodeFixer(src)
    fx.apply(corr(src, 0, 1, "X"))
    step2 = corr(fx.current, 1, 2, "Y", "C2")
    fx.apply(step2)
    assert fx.current == "XYc"
    assert fx.undo() and fx.current == "Xbc"
    assert fx.undo() and fx.current == "abc"
    assert not fx.undo() and not fx.can_undo


def test_stale_correction_is_refused():
    src = "abc"
    fx = CodeFixer(src)
    stale = corr(src, 0, 1, "X", "C1")
    fx.apply(corr(src, 2, 3, "Z", "C2"))
    with pytest.raises(StaleCorrectionError):
        fx.apply(stale)
    assert fx.current == "abZ"


def test_assumption_requires_explicit_confirmation():
    src = "abc"
    fx = CodeFixer(src)
    c = corr(src, 0, 0, "int ", assumption="guessing a type")
    with pytest.raises(ConfirmationRequired):
        fx.apply(c)
    assert fx.current == src
    assert fx.apply(c, confirmed=True) == "int abc"


def test_advisory_correction_cannot_be_applied():
    fx = CodeFixer("abc")
    with pytest.raises(CodeFixerError):
        fx.apply(Correction("C1", "advice", [], 0.5))


def test_apply_many_independent_and_overlapping():
    src = "0123456789"
    fx = CodeFixer(src)
    a, b = corr(src, 0, 1, "A", "A"), corr(src, 5, 6, "B", "B")
    overlap = corr(src, 0, 3, "ZZZ", "O")
    risky = corr(src, 8, 9, "R", "R", assumption="x")
    new, accepted, skipped = fx.apply_many([a, b, overlap, risky])
    assert new == "A1234B6789" and [c.correction_id for c in accepted] == ["A", "B"]
    reasons = {c.correction_id: r for c, r in skipped}
    assert "overlaps" in reasons["O"] and "confirmation" in reasons["R"]
    assert fx.applied_count == 1 and fx.undo() and fx.current == src  # one undo step for the batch


def test_apply_many_with_confirmed_ids():
    src = "0123"
    fx = CodeFixer(src)
    risky = corr(src, 1, 2, "R", "R", assumption="x")
    new, accepted, _ = fx.apply_many([risky], confirmed_ids={"R"})
    assert new == "0R23" and accepted


def test_reject_and_reset():
    src = "abc"
    fx = CodeFixer(src)
    fx.reject("C9")
    assert fx.is_rejected("C9") and not fx.is_rejected("C1")
    fx.apply(corr(src, 0, 1, "Q"))
    fx.reset()
    assert fx.current == src and fx.applied_count == 0 and fx.rejected_ids == set()


def test_indentation_and_formatting_untouched_outside_the_edit():
    src = "int main() {\n\tif (x)\n\t\ty = 1\n}\n"
    off = src.index("y = 1") + len("y = 1")
    fx = CodeFixer(src)
    new = fx.apply(corr(src, off, off, ";"))
    assert new == "int main() {\n\tif (x)\n\t\ty = 1;\n}\n"


def test_removal_edit_deletes_whole_line_when_it_would_leave_blank_line():
    src = "a;\n    break;\nb;\n"
    ctx = CorrectionContext(src, [])
    s = src.index("break")
    assert apply_edits(src, [ctx.removal_edit(s, s + len("break;"))]) == "a;\nb;\n"


# ====================================================================== UI controller (stub analyzer)
class _StubResult:
    def __init__(self, source):
        self.source = source
        self.diagnostics = []
        if "BAD" in source:
            i = source.index("BAD")
            d = Diagnostic("SYN004", "Syntax", "error", "bad token", "d", 1, i + 1, source)
            d.corrections = [Correction("C1", "replace BAD", [TextEdit(i, i + 3, "OK")], 0.9, source_hash=source_hash(source)),
                             Correction("C2", "risky", [TextEdit(i, i + 3, "RISK")], 0.5, assumption="guess",
                                        source_hash=source_hash(source))]
            self.diagnostics.append(d)

    def error_count(self):
        return len(self.diagnostics)

    def warning_count(self):
        return 0

    status = property(lambda self: "Failed" if self.diagnostics else "Passed")
    complete = property(lambda self: True)
    passed = property(lambda self: not self.diagnostics)


class _StubPipeline:
    def analyze(self, source):
        return _StubResult(source)


def new_controller(state=None):
    return AppController({} if state is None else state, _StubPipeline())


def test_controller_apply_reanalyses_and_updates_editor():
    c = new_controller()
    c.load("x BAD y")
    c.analyze()
    v = c.state["editor_version"]
    assert c.apply_correction("C1") is True
    assert c.source == "x OK y" and c.state["editor_version"] == v + 1
    assert c.result.error_count() == 0 and c.fixer.original == "x BAD y"


def test_controller_requires_confirmation_for_assumptions():
    c = new_controller()
    c.load("BAD")
    c.analyze()
    assert c.apply_correction("C2") is False and c.source == "BAD"
    assert any("confirm" in text for _, text in c.pop_messages())
    assert c.apply_correction("C2", confirmed=True) and c.source == "RISK"


def test_controller_undo_reset_and_final_report():
    c = new_controller()
    c.load("a BAD")
    c.analyze()
    c.apply_correction("C1")
    rep = c.final_report()
    assert (rep["errors_detected"], rep["corrections_applied"], rep["remaining_errors"], rep["passed"]) == (1, 1, 0, True)
    assert c.undo() and c.source == "a BAD" and c.result.error_count() == 1
    c.apply_correction("C1")
    c.reset()
    assert c.source == "a BAD" and c.result is None and c.fixer.applied_count == 0


def test_controller_reject_hides_suggestion_without_touching_code():
    c = new_controller()
    c.load("BAD")
    c.analyze()
    diag = c.result.diagnostics[0]
    c.reject_correction("C1")
    assert [x.correction_id for x in c.visible_corrections(diag)] == ["C2"] and c.source == "BAD"
    assert c.final_report()["corrections_rejected"] == 1


def test_controller_refuses_suggestions_after_manual_edit():
    c = new_controller()
    c.load("BAD")
    c.analyze()
    c.set_source("BAD!")  # user typed
    assert c.state["dirty"] and c.apply_correction("C1") is False and c.source == "BAD!"
    c.analyze()
    assert not c.state["dirty"] and c.apply_correction("C1") is True


def test_controller_state_survives_script_rerun():
    state = {}
    c1 = new_controller(state)
    c1.load("p BAD")
    c1.analyze()
    c1.apply_correction("C1")
    c2 = new_controller(state)  # Streamlit reruns the script and rebuilds the controller on the same session_state
    assert c2.source == "p OK" and c2.fixer.applied_count == 1 and c2.result is c1.result
    assert c2.fixer.original == "p BAD"


def test_controller_unknown_correction_and_empty_undo():
    c = new_controller()
    c.load("fine")
    c.analyze()
    assert c.apply_correction("C77") is False and c.undo() is False


def test_controller_apply_all_safe_skips_assumption_bearing_corrections():
    c = new_controller()
    c.load("a BAD b")
    c.analyze()
    assert c.apply_all_safe() == 1 and c.source == "a OK b"  # C1 (safe) applied, C2 (assumption) not
    assert c.fixer.applied_count == 1
    assert c.apply_all_safe() == 0  # nothing left to do
