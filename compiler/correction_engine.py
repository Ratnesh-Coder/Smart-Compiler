"""Hybrid, context-aware correction engine.

For every diagnostic the engine

1. **generates** candidates with the registered rules (rule-based, edit-distance, grammar-based,
   AST-based and symbol-table-based; see :mod:`compiler.correction_rules`),
2. **removes invalid candidates** (edits outside the text, no-op edits and - when a verifier is
   available - edits that do not actually resolve the diagnostic or that add new errors),
3. **ranks** the survivors with a configurable weighted score, and
4. attaches the top candidates with a **heuristic confidence** and an **explanation**.

The confidence value is a weighted heuristic score, *not* a statistically calibrated probability.
The engine never modifies source code; applying a correction is the job of
:class:`compiler.code_fixer.CodeFixer` after explicit user approval.
"""
from __future__ import annotations

import difflib
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from compiler.code_fixer import CodeFixerError, apply_edits, source_hash
from compiler.correction_rules import (GENERIC_RULES, RULES, Candidate, CorrectionContext)
from compiler.error_manager import ERROR, Correction, Diagnostic

log = logging.getLogger(__name__)


@dataclass
class ScoringWeights:
    """Configurable weights of the ranking model (normalised internally)."""

    grammar: float = 0.35
    context: float = 0.25
    symbol: float = 0.20
    similarity: float = 0.20

    def total(self) -> float:
        return (self.grammar + self.context + self.symbol + self.similarity) or 1.0


@dataclass
class VerificationOutcome:
    """Summary of re-analysing a modified source (see ``CompilerPipeline.verify``)."""

    error_count: int
    signatures: Counter = field(default_factory=Counter)  # (severity, error_id, message) -> count
    by_category: Counter = field(default_factory=Counter)  # category -> error count

    def errors_up_to(self, category: str) -> int:
        """Errors in ``category`` and all *earlier* compiler phases (lexical < syntax < semantic).

        Fixing an early-phase error can legitimately unmask errors of later phases (the parser
        suppresses cascades and semantic analysis is skipped on an unusable tree), so a candidate
        is judged only on the phases up to the one it repairs.
        """
        if not self.by_category:
            return self.error_count
        order = ("Lexical", "Syntax", "Semantic")
        upto = order[: order.index(category) + 1] if category in order else order
        return sum(self.by_category.get(c, 0) for c in upto)


Verifier = Callable[[str], VerificationOutcome]


class CorrectionEngine:
    def __init__(self, weights: Optional[ScoringWeights] = None, verifier: Optional[Verifier] = None,
                 max_suggestions: int = 4) -> None:
        self.weights = weights or ScoringWeights()
        self.verifier = verifier
        self.max_suggestions = max_suggestions

    # ------------------------------------------------------------------ public API
    def attach(self, diagnostics: Iterable[Diagnostic], ctx: CorrectionContext,
               baseline: Optional[VerificationOutcome] = None) -> None:
        """Compute and attach ranked corrections to each diagnostic (in place)."""
        diagnostics = list(diagnostics)
        if baseline is None and self.verifier is not None:
            baseline = self.verifier(ctx.source)
        counter = 0
        for d in diagnostics:
            corrections = self.suggest(d, ctx, baseline)
            for c in corrections:
                counter += 1
                c.correction_id = f"C{counter}"
            d.corrections = corrections
            if corrections:
                top = corrections[0]
                d.suggestion = top.description
                d.corrected_code = top.preview or None
                d.confidence = top.confidence

    def suggest(self, d: Diagnostic, ctx: CorrectionContext,
                baseline: Optional[VerificationOutcome] = None) -> list[Correction]:
        """Generate, validate, rank and explain corrections for one diagnostic."""
        scored: list[tuple[float, Candidate, float]] = []
        seen: set[tuple[tuple[int, int, str], ...]] = set()
        for cand in self._generate(d, ctx):
            key = tuple(sorted((e.start, e.end, e.replacement) for e in cand.edits))
            if cand.edits and key in seen:
                continue
            seen.add(key)
            grammar = self._validate(cand, d, ctx, baseline)
            if grammar is None:
                continue
            w = self.weights
            score = (w.grammar * grammar + w.context * cand.context + w.symbol * cand.symbol
                     + w.similarity * cand.similarity) / w.total()
            scored.append((score, cand, grammar))
        scored.sort(key=lambda t: -t[0])
        digest = source_hash(ctx.source)
        result: list[Correction] = []
        for score, cand, grammar in scored[: self.max_suggestions]:
            result.append(Correction(
                correction_id="", description=cand.description, edits=cand.edits,
                confidence=round(min(0.99, max(0.05, score)), 2), explanation=cand.explanation,
                method=cand.method, assumption=cand.assumption,
                preview=self._preview(ctx.source, cand), source_hash=digest,
                score_breakdown={"grammar": round(grammar, 2), "context": round(cand.context, 2),
                                 "symbol": round(cand.symbol, 2), "similarity": round(cand.similarity, 2)}))
        return result

    # ------------------------------------------------------------------ internals
    def _generate(self, d: Diagnostic, ctx: CorrectionContext) -> list[Candidate]:
        out: list[Candidate] = []
        funcs = list(RULES.get(d.kind, []))
        funcs += [fn for kinds, fn in GENERIC_RULES if d.kind in kinds]
        for fn in funcs:
            try:
                out.extend(fn(d, ctx))
            except Exception:  # a faulty rule must not take the whole analysis down - but never hide it
                log.exception("Correction rule %s failed for diagnostic %s", getattr(fn, "__name__", fn), d.error_id)
        return out

    def _validate(self, cand: Candidate, d: Diagnostic, ctx: CorrectionContext,
                  baseline: Optional[VerificationOutcome]) -> Optional[float]:
        """Return the grammar score of a valid candidate, or ``None`` to discard it."""
        if not cand.edits:
            return cand.grammar  # advisory suggestion
        try:
            new_source = apply_edits(ctx.source, cand.edits)
        except (ValueError, CodeFixerError):
            return None  # edit outside the text or overlapping edits inside one candidate: invalid
        if new_source == ctx.source:
            return None
        if self.verifier is None or baseline is None:
            return cand.grammar
        after = self.verifier(new_source)
        key = (d.severity, *d.signature)
        before_n, after_n = baseline.signatures.get(key, 0), after.signatures.get(key, 0)
        if after_n >= before_n:
            return None  # does not resolve this diagnostic
        if d.severity == ERROR:
            # an error fix must strictly reduce the errors of its phase (and earlier phases); merely
            # replacing one error by another is not a correction
            return 1.0 if after.errors_up_to(d.category) < baseline.errors_up_to(d.category) else None
        return 1.0 if after.errors_up_to(d.category) <= baseline.errors_up_to(d.category) else None

    @staticmethod
    def _preview(source: str, cand: Candidate) -> str:
        if not cand.edits:
            return ""
        try:
            new = apply_edits(source, cand.edits)
        except (ValueError, CodeFixerError):
            return ""
        old_lines, new_lines = source.splitlines(), new.splitlines()
        changed = []
        for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                changed.extend(new_lines[j1:j2])
        return "\n".join(changed[:4])
