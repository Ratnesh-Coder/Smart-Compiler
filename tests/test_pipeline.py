"""Full-pipeline and integration tests (require PLY)."""
import json

import pytest

pytest.importorskip("ply")

from compiler.code_fixer import CodeFixer  # noqa: E402
from tests.helpers import analyze, ids, kinds, pipeline, sample  # noqa: E402
from ui.session import AppController  # noqa: E402


# ===================================================================== phases and status
def test_valid_program_passes_all_implemented_checks():
    r = analyze(sample("valid_program.c"))
    assert r.error_count() == 0 and r.warning_count() == 0
    assert r.passed and r.complete and r.status == "Passed" and r.skipped_checks == []
    assert [p.status for p in r.phases if p.name != "correction"] == ["completed"] * 3
    assert r.symbol_table is not None and r.symbol_table.lookup_global("square") is not None


def test_valid_constructs_do_not_produce_false_errors():
    src = """
    int g = 5;
    int fib(int n) { if (n < 2) return n; return fib(n - 1) + fib(n - 2); }
    void greet(void) { printf("hi\\n"); }
    int main() {
        int a = 1, b[3] = {1, 2, 3};
        char name[8] = "abc";
        float f = 2;
        double d = f * g + 1;
        int i;
        for (i = 0; i < 3; i++) { if (b[i] > 1) break; else continue; }
        { int local = a; a = local; }
        scanf("%d", &a);
        greet();
        a = fib(a) + (int_like(1));
        return 0;
    }
    int int_like(int v) { return v; }
    """
    # int_like is used before it is defined -> only that is an error (as in C)
    assert ids(analyze(src)) == ["SEM010"]
    assert analyze(src.replace("a = fib(a) + (int_like(1));", "a = fib(a);")).error_count() == 0


def test_each_error_category_is_classified():
    assert set(ids(analyze(sample("lexical_errors.c")))) == {"LEX001", "LEX002", "LEX003"}
    syn = analyze(sample("syntax_errors.c"))
    assert {d.category for d in syn.diagnostics} == {"Syntax"}
    sem = analyze(sample("semantic_errors.c"))
    assert {d.category for d in sem.diagnostics} == {"Semantic"} and sem.warning_count() >= 2
    assert sem.summary()["semantic"] == sem.error_count("Semantic")


def test_semantic_phase_is_skipped_and_reported_when_syntax_tree_is_unusable():
    r = analyze("int main() {\n    itn x = 5;\n    y = 2;\n}\n")
    assert [p.status for p in r.phases[:3]] == ["completed", "partial", "skipped"]
    assert r.skipped_checks and "Type checking" in r.skipped_checks
    assert r.status == "Failed" and not r.passed and not r.complete
    assert all(d.category != "Semantic" for d in r.diagnostics)  # undeclared y is NOT reported (honestly skipped)


def test_semantic_analysis_runs_on_repaired_tree_and_says_so():
    r = analyze("int main() {\n    int a = 1\n    b = 2;\n    return 0;\n}\n")
    assert ids(r) == ["SYN001", "SEM001"]
    assert any("reconstructed after error repair" in n for n in r.notes)
    assert r.phases[2].status == "partial"


def test_multiple_errors_across_phases_are_all_reported():
    src = "int main() {\n    int a = 1\n    int b = a + undefinedvar;\n    return 0;\n}\n"
    assert set(ids(analyze(src))) == {"SYN001", "SEM001"}


def test_lexical_cascade_is_suppressed_not_duplicated():
    r = analyze('int main() {\n    printf("unterminated);\n    return 0;\n}\n')
    assert ids(r) == ["LEX003"]
    assert any("suppressed" in n for n in r.notes)
    r = analyze("int main() {\n    return 0;\n/* never closed\n")
    assert ids(r) == ["LEX004"]


def test_lexical_error_does_not_block_parsing():
    r = analyze("int main() {\n    int a = 5 @;\n    int b = a;\n    return b;\n}\n")
    assert ids(r) == ["LEX001"] and r.phases[1].status == "completed"


def test_empty_and_comment_only_programs():
    assert analyze("").skipped_checks == ["All compiler phases"]
    r = analyze("// nothing here\n/* at all */")
    assert r.error_count() == 0 and r.tokens == [] and not r.complete or r.status in ("Passed", "Incomplete")


def test_result_is_json_serialisable_and_consistent():
    r = analyze(sample("semantic_errors.c"))
    data = json.loads(json.dumps(r.to_dict()))
    assert data["summary"]["errors"] == r.error_count() == len(r.errors)
    assert data["diagnostics"] and "corrections" in data["diagnostics"][0]


@pytest.mark.parametrize("src", [
    "", "   \n\t", "\x00", "int main() { return 0; }" * 50, "{" * 200, "(" * 300, "int main() {" + "a = 1 + " * 400 + "1; }",
    "int \u00e9t\u00e9 = 3;", "/*", "\"", "'", "#", "@@@@", "int main() { int a[2147483648]; }",
    "int main() { " + "{ " * 150 + "}" * 150 + " }",
])
def test_pipeline_never_raises_on_hostile_input(src):
    r = analyze(src)
    assert r.internal_error is None
    assert isinstance(r.summary(), dict)


# ===================================================================== diagnostics + suggestions
def test_diagnostic_fields_are_complete():
    r = analyze(sample("semantic_errors.c"))
    for d in r.diagnostics:
        assert d.error_id and d.category and d.severity and d.message and d.description
        assert d.line >= 1 and d.column >= 1 and d.offending_code is not None
        assert d.suggestion
    first = next(d for d in r.diagnostics if d.kind == "undeclared_identifier")
    assert first.confidence is not None and 0 < first.confidence < 1 and first.corrected_code


def test_spec_missing_semicolon_example_and_suggestion():
    r = analyze("int main() {\n    int x = 10\n    return 0;\n}\n")
    d = r.diagnostics[0]
    assert d.error_id == "SYN001" and "Insert ';'" in d.suggestion
    assert d.corrections[0].preview.strip() == "int x = 10;"


def test_keyword_typos_are_corrected_and_ranked_first():
    for bad, good in [("itn", "int"), ("whlie", "while"), ("retrun", "return"), ("flaot", "float")]:
        src = {"itn": "int main() {\n    itn x = 5;\n    return 0;\n}\n",
               "whlie": "int main() {\n    whlie (1) {\n        return 0;\n    }\n}\n",
               "retrun": "int f() {\n    retrun 0;\n}\n",
               "flaot": "int main() {\n    flaot x = 1.5;\n    return 0;\n}\n"}[bad]
        r = analyze(src)
        offering = [d for d in r.diagnostics if any(f"keyword '{good}'" in c.description for c in d.corrections)]
        assert offering, (bad, [(d.error_id, [c.description for c in d.corrections]) for d in r.diagnostics])
        for d in offering:  # the typo fix must outrank every other candidate for that diagnostic
            assert f"keyword '{good}'" in d.corrections[0].description and d.corrections[0].method == "edit_distance"


def test_candidates_are_verified_by_reanalysis():
    r = analyze("int main() {\n    int x = 10\n    return 0;\n}\n")
    for d in r.diagnostics:
        for c in d.corrections:
            assert c.score_breakdown["grammar"] == 1.0  # every surviving candidate really resolves the error
            fixed = CodeFixer(r.source).preview(c)
            assert analyze(fixed).error_count() < r.error_count()


def test_alternatives_are_returned_when_appropriate():
    r = analyze("int main() {\n    int count = 0;\n    cuont = 5;\n    return 0;\n}\n")
    d = r.diagnostics[0]
    assert len(d.corrections) >= 2 and d.corrections[0].confidence >= d.corrections[1].confidence


def test_undeclared_name_never_suggests_out_of_scope_identifier():
    r = analyze("int main() {\n    { int hidden = 1; }\n    hiddn = 2;\n    return 0;\n}\n")
    d = r.diagnostics[0]
    assert all("'hidden'" not in c.description for c in d.corrections if c.method == "edit_distance")


# ===================================================================== apply + re-analyse loops
def _fix_until_clean(source, max_rounds=15):
    fixer, history = CodeFixer(source), []
    for _ in range(max_rounds):
        r = analyze(fixer.current)
        history.append(r.error_count())
        if r.error_count() == 0:
            return fixer, history
        options = [c for d in r.errors for c in d.corrections if c.edits]
        if not options:
            return fixer, history
        fixer.apply(options[0], confirmed=True)
    return fixer, history


@pytest.mark.parametrize("name", ["lexical_errors.c", "syntax_errors.c", "semantic_errors.c"])
def test_iterative_correction_converges_to_a_clean_program(name):
    fixer, history = _fix_until_clean(sample(name))
    assert fixer.applied_count >= 1
    # errors of earlier phases may unmask later-phase errors, so the total is non-increasing overall
    assert all(b <= a for a, b in zip(history, history[1:])), history
    assert history[-1] < history[0] and history[-1] == 0, history


@pytest.mark.parametrize("src", [
    "int main() {\n    int x = 10\n    return 0;\n}\n",
    "int main() {\n    int a = 1;\n    if (a > 0 {\n        a = 2;\n    }\n    return 0;\n}\n",
    "int main() {\n    int a = 1;\n    return 0;\n",
    "int main() {\n    printf(\"hi);\n    return 0;\n}\n",
    "int main() {\n    total = 3;\n    return total;\n}\n",
])
def test_single_error_programs_are_fully_repaired(src):
    fixer, history = _fix_until_clean(src)
    final = analyze(fixer.current)
    assert final.error_count() == 0 and final.passed, (fixer.current, history)


def test_original_is_preserved_and_undo_restores_it():
    src = "int main() {\n    int x = 10\n    return 0;\n}\n"
    fixer, _ = _fix_until_clean(src)
    assert fixer.original == src and fixer.current != src
    while fixer.undo():
        pass
    assert fixer.current == src


# ===================================================================== controller + real pipeline
def test_controller_with_real_pipeline_end_to_end():
    state = {}
    c = AppController(state, pipeline())
    c.load(sample("syntax_errors.c"))
    first = c.analyze()
    n = first.error_count()
    assert n >= 3 and c.final_report()["errors_detected"] == n
    cid = next(cc.correction_id for d in first.diagnostics for cc in d.corrections if cc.edits)
    assert c.apply_correction(cid, confirmed=True)
    assert c.result.error_count() < n and c.fixer.applied_count == 1
    c2 = AppController(state, pipeline())  # simulated Streamlit rerun
    assert c2.source == c.source and c2.result is c.result
    assert c2.undo() and c2.source == sample("syntax_errors.c")
    assert c2.result.error_count() == n
