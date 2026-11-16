"""Compiler pipeline controller.

Coordinates the phases ``lexical -> syntax -> semantic -> correction`` and always returns a
consistent :class:`AnalysisResult`, even for malformed input.

Recovery policy
---------------
* The lexer never aborts: invalid input is reported and the token stream continues.
* The parser reports one diagnostic per genuine mistake (token-insertion repair, see
  :mod:`compiler.parser`).
* **Semantic analysis runs only if the AST is reliable**, i.e. the parser produced a tree
  without panic-mode recovery. If it is skipped, the skipped checks are listed explicitly in
  ``AnalysisResult.skipped_checks`` - they are never silently omitted.
* Syntax errors that are merely *consequences* of a lexical error (same line, or the line after
  an unterminated string, or end-of-file errors after an unterminated comment) are suppressed
  and counted in the result notes.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.ast_nodes import ProgramNode
from compiler.correction_engine import CorrectionEngine, ScoringWeights, VerificationOutcome
from compiler.correction_rules import CorrectionContext
from compiler.error_manager import (ERROR, LEXICAL, SEMANTIC, SUGGESTION, SYNTAX, WARNING, Diagnostic,
                                    ErrorManager)
from compiler.lexer import CLexer
from compiler.parser import CParser
from compiler.semantic_analyzer import SemanticAnalyzer
from compiler.symbol_table import SymbolTable
from compiler.tokens import Token
from utils.validators import normalize_source, validate_source

log = logging.getLogger(__name__)

COMPLETED, SKIPPED, PARTIAL, FAILED = "completed", "skipped", "partial", "failed"
SEMANTIC_CHECKS = ["Symbol table construction", "Declaration/scope checking", "Type checking",
                   "Function call and return checking", "Array access checking"]


@dataclass
class PhaseStatus:
    name: str
    status: str
    note: str = ""


@dataclass
class AnalysisResult:
    """Structured outcome of analysing one source text."""

    source: str
    tokens: list[Token] = field(default_factory=list)
    ast: Optional[ProgramNode] = None
    symbol_table: Optional[SymbolTable] = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    phases: list[PhaseStatus] = field(default_factory=list)
    skipped_checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    internal_error: Optional[str] = None

    # ---- counts
    def error_count(self, category: Optional[str] = None) -> int:
        return sum(1 for d in self.diagnostics if d.severity == ERROR and (category is None or d.category == category))

    def warning_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == WARNING)

    def suggestion_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == SUGGESTION)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == WARNING]

    @property
    def complete(self) -> bool:
        """True if every phase ran to completion (nothing skipped, no internal failure)."""
        return not self.skipped_checks and self.internal_error is None and all(
            p.status == COMPLETED for p in self.phases if p.name != "correction")

    @property
    def passed(self) -> bool:
        """Passed all *implemented* checks: no errors and the analysis was complete."""
        return self.error_count() == 0 and self.complete

    @property
    def status(self) -> str:
        if self.internal_error:
            return "Internal error"
        if self.error_count():
            return "Failed"
        if not self.complete:
            return "Incomplete"
        return "Passed with warnings" if self.warning_count() else "Passed"

    def summary(self) -> dict[str, Any]:
        return {"status": self.status, "errors": self.error_count(), "lexical": self.error_count(LEXICAL),
                "syntax": self.error_count(SYNTAX), "semantic": self.error_count(SEMANTIC),
                "warnings": self.warning_count(), "suggestions": self.suggestion_count(),
                "tokens": len(self.tokens), "passed": self.passed}

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "diagnostics": [d.to_dict() for d in self.diagnostics],
                "phases": [vars(p) for p in self.phases], "skipped_checks": self.skipped_checks,
                "notes": self.notes}


class CompilerPipeline:
    """Runs the full compiler front end plus the intelligent correction engine."""

    def __init__(self, enable_corrections: bool = True, verify_corrections: bool = True,
                 weights: Optional[ScoringWeights] = None, max_suggestions: int = 4) -> None:
        self.enable_corrections = enable_corrections
        self.verify_corrections = verify_corrections
        self.weights = weights
        self.max_suggestions = max_suggestions
        self._lexer = CLexer()
        self._parser = CParser()
        self._lock = threading.RLock()  # the PLY lexer/parser objects are stateful; Streamlit sessions are threads

    # ------------------------------------------------------------------ public API
    def analyze(self, source: str) -> AnalysisResult:
        """Analyse ``source`` and (optionally) attach ranked correction suggestions."""
        with self._lock:
            result = self._run(source)
            if self.enable_corrections and result.diagnostics and result.internal_error is None:
                self._attach_corrections(result)
            return result

    def verify(self, source: str) -> VerificationOutcome:
        """Analyse without corrections and summarise the diagnostics (used to validate candidates)."""
        with self._lock:
            return self._outcome(self._run(source))

    # ------------------------------------------------------------------ phases
    def _run(self, source: str) -> AnalysisResult:
        ok, message = validate_source(source)
        if not ok:
            res = AnalysisResult(source=source if isinstance(source, str) else "")
            res.notes.append(message)
            res.skipped_checks = ["All compiler phases"]
            res.phases = [PhaseStatus(n, SKIPPED, message) for n in ("lexical", "syntax", "semantic")]
            return res

        src = normalize_source(source)
        errors = ErrorManager(src)
        result = AnalysisResult(source=src)

        # -- lexical analysis ---------------------------------------------------
        try:
            self._lexer.errors = errors
            result.tokens = self._lexer.tokenize(src)
            lex_n = errors.error_count(LEXICAL)
            result.phases.append(PhaseStatus("lexical", COMPLETED, f"{len(result.tokens)} tokens, {lex_n} error(s)"))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            return self._internal_failure(result, errors, "lexical", exc)

        if not result.tokens:
            result.ast = ProgramNode()
            result.phases.append(PhaseStatus("syntax", SKIPPED, "No tokens (empty program or only comments)."))
            result.phases.append(PhaseStatus("semantic", SKIPPED, "Nothing to analyse."))
            result.notes.append("The program contains no tokens.")
            result.diagnostics = errors.sorted()
            return result

        # -- syntax analysis ----------------------------------------------------
        try:
            parsed = self._parser.parse(result.tokens, src, errors)
        except Exception as exc:  # noqa: BLE001
            return self._internal_failure(result, errors, "syntax", exc)
        suppressed = self._suppress_cascades(errors)
        if suppressed:
            result.notes.append(f"{suppressed} syntax error(s) caused by a lexical error were suppressed.")
        result.notes.extend(parsed.notes)
        result.ast = parsed.ast
        syn_n = errors.error_count(SYNTAX)
        status = COMPLETED if parsed.ast_reliable or syn_n == 0 else PARTIAL
        result.phases.append(PhaseStatus("syntax", status, f"{syn_n} error(s); AST "
                                         + ("reliable" if parsed.ast_reliable else "incomplete")))

        # -- semantic analysis --------------------------------------------------
        if parsed.ast is not None and parsed.ast_reliable:
            try:
                analyzer = SemanticAnalyzer(errors)
                result.symbol_table = analyzer.analyze(parsed.ast)
                recovered = errors.error_count(LEXICAL) + errors.error_count(SYNTAX) > 0
                result.phases.append(PhaseStatus("semantic", PARTIAL if recovered else COMPLETED,
                                                 f"{errors.error_count(SEMANTIC)} error(s), "
                                                 f"{errors.warning_count(SEMANTIC)} warning(s)"))
                if recovered:
                    result.notes.append("Semantic analysis ran on a syntax tree reconstructed after error repair; "
                                        "re-analyse after fixing the earlier errors for a definitive result.")
            except Exception as exc:  # noqa: BLE001
                return self._internal_failure(result, errors, "semantic", exc)
        else:
            result.skipped_checks = list(SEMANTIC_CHECKS)
            result.phases.append(PhaseStatus("semantic", SKIPPED,
                                             "Skipped: the syntax tree is unusable because of syntax errors."))
        result.diagnostics = errors.sorted()
        return result

    @staticmethod
    def _suppress_cascades(errors: ErrorManager) -> int:
        lex = [d for d in errors if d.category == LEXICAL and d.severity == ERROR]
        if not lex:
            return 0
        lex_lines = {d.line for d in lex}
        string_lines = {d.line for d in lex if d.kind == "unterminated_string"}
        eof_cascade = any(d.kind == "unterminated_comment" for d in lex)

        def cascade(d: Diagnostic) -> bool:
            if d.category != SYNTAX:
                return False
            return (d.line in lex_lines or (d.line - 1) in string_lines
                    or (eof_cascade and d.kind in ("unexpected_eof", "missing_brace")))

        return len(errors.discard_where(cascade))

    @staticmethod
    def _internal_failure(result: AnalysisResult, errors: ErrorManager, phase: str, exc: Exception) -> AnalysisResult:
        log.exception("Internal failure in %s phase", phase)
        result.internal_error = f"{phase} phase failed: {type(exc).__name__}: {exc}"
        result.phases.append(PhaseStatus(phase, FAILED, result.internal_error))
        result.skipped_checks = result.skipped_checks or ["Remaining compiler phases"]
        result.diagnostics = errors.sorted()
        return result

    # ------------------------------------------------------------------ corrections
    @staticmethod
    def _outcome(result: AnalysisResult) -> VerificationOutcome:
        sigs = Counter((d.severity, *d.signature) for d in result.diagnostics)
        cats = Counter(d.category for d in result.diagnostics if d.severity == ERROR)
        return VerificationOutcome(error_count=result.error_count(), signatures=sigs, by_category=cats)

    def _attach_corrections(self, result: AnalysisResult) -> None:
        ctx = CorrectionContext(result.source, result.tokens, result.ast, result.symbol_table,
                                can_verify=self.verify_corrections)
        engine = CorrectionEngine(self.weights, self.verify if self.verify_corrections else None,
                                  self.max_suggestions)
        try:
            engine.attach(result.diagnostics, ctx, self._outcome(result) if self.verify_corrections else None)
            result.phases.append(PhaseStatus("correction", COMPLETED,
                                             f"{sum(len(d.corrections) for d in result.diagnostics)} suggestion(s)"))
        except Exception as exc:  # noqa: BLE001
            log.exception("Correction engine failure")
            result.phases.append(PhaseStatus("correction", FAILED, f"{type(exc).__name__}: {exc}"))
            result.notes.append("The correction engine failed; diagnostics are still valid.")
