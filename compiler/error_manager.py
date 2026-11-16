"""Centralised diagnostic management.

Every compiler phase reports problems through :class:`ErrorManager`. A diagnostic is described
by a structured :class:`Diagnostic`; proposed fixes are :class:`Correction` objects made of
position-based :class:`TextEdit` operations (never global string replacement).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from utils.source_location import LineIndex

# Categories and severities -----------------------------------------------------------
LEXICAL = "Lexical"
SYNTAX = "Syntax"
SEMANTIC = "Semantic"
CATEGORIES = (LEXICAL, SYNTAX, SEMANTIC)

ERROR = "error"
WARNING = "warning"
SUGGESTION = "suggestion"
SEVERITIES = (ERROR, WARNING, SUGGESTION)


@dataclass(frozen=True)
class TextEdit:
    """Replace ``source[start:end]`` with ``replacement`` (an insertion when start == end)."""

    start: int
    end: int
    replacement: str = ""

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"Invalid edit range [{self.start}, {self.end})")

    def apply(self, source: str) -> str:
        if self.end > len(source):
            raise ValueError("Edit range exceeds source length")
        return source[: self.start] + self.replacement + source[self.end:]


@dataclass
class Correction:
    """A candidate fix for one diagnostic."""

    correction_id: str
    description: str
    edits: list[TextEdit]
    confidence: float = 0.5
    explanation: str = ""
    method: str = "rule"
    assumption: Optional[str] = None  # set when the fix may change program behaviour
    preview: str = ""
    source_hash: str = ""
    score_breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def is_auto_applicable(self) -> bool:
        return bool(self.edits)

    @property
    def requires_confirmation(self) -> bool:
        return self.assumption is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id, "description": self.description,
            "edits": [{"start": e.start, "end": e.end, "replacement": e.replacement} for e in self.edits],
            "confidence": self.confidence, "explanation": self.explanation, "method": self.method,
            "assumption": self.assumption, "preview": self.preview,
            "score_breakdown": dict(self.score_breakdown),
        }


@dataclass
class Diagnostic:
    """A single compiler diagnostic."""

    error_id: str
    category: str
    severity: str
    message: str
    description: str
    line: int
    column: int
    offending_code: str
    suggestion: Optional[str] = None
    corrected_code: Optional[str] = None
    confidence: Optional[float] = None
    kind: str = ""
    offset: int = -1
    length: int = 0
    context: dict[str, Any] = field(default_factory=dict)
    corrections: list[Correction] = field(default_factory=list)

    @property
    def signature(self) -> tuple[str, str]:
        return (self.error_id, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_id": self.error_id, "category": self.category, "severity": self.severity,
            "message": self.message, "description": self.description, "line": self.line,
            "column": self.column, "offending_code": self.offending_code,
            "suggestion": self.suggestion, "corrected_code": self.corrected_code,
            "confidence": self.confidence, "kind": self.kind, "offset": self.offset,
            "length": self.length, "corrections": [c.to_dict() for c in self.corrections],
        }


class ErrorManager:
    """Collects, deduplicates, filters and counts diagnostics."""

    def __init__(self, source: str = "") -> None:
        self._diagnostics: list[Diagnostic] = []
        self._seen: set[tuple[Any, ...]] = set()
        self._index = LineIndex(source)

    # ------------------------------------------------------------------ configuration
    def set_source(self, source: str) -> None:
        self._index = LineIndex(source)

    # ------------------------------------------------------------------ adding
    def add(self, diagnostic: Diagnostic) -> bool:
        """Add a diagnostic. Returns ``False`` if an identical one already exists."""
        key = (diagnostic.error_id, diagnostic.line, diagnostic.column, diagnostic.message)
        if key in self._seen:
            return False
        self._seen.add(key)
        self._diagnostics.append(diagnostic)
        return True

    def report(self, kind: str, line: int, column: int, *, message: Optional[str] = None,
               description: Optional[str] = None, suggestion: Optional[str] = None,
               offending_code: Optional[str] = None, offset: int = -1, length: int = 0,
               context: Optional[dict[str, Any]] = None,
               severity: Optional[str] = None) -> Optional[Diagnostic]:
        """Create a diagnostic from the error catalogue and add it.

        Returns the new :class:`Diagnostic`, or ``None`` if it was a duplicate.
        """
        from compiler.error_classifier import get_spec  # local import avoids a cycle

        spec = get_spec(kind)
        diag = Diagnostic(
            error_id=spec.error_id, category=spec.category, severity=severity or spec.severity,
            message=message or spec.title, description=description or spec.explanation,
            line=line, column=column,
            offending_code=offending_code if offending_code is not None else self._index.line_text(line),
            suggestion=suggestion, kind=kind, offset=offset, length=length,
            context=dict(context or {}),
        )
        return diag if self.add(diag) else None

    # ------------------------------------------------------------------ retrieving
    def all(self) -> list[Diagnostic]:
        return list(self._diagnostics)

    def sorted(self) -> list[Diagnostic]:
        return sorted(self._diagnostics, key=lambda d: (d.line, d.column, d.error_id))

    def by_category(self, category: str) -> list[Diagnostic]:
        return [d for d in self._diagnostics if d.category == category]

    def by_severity(self, severity: str) -> list[Diagnostic]:
        return [d for d in self._diagnostics if d.severity == severity]

    def errors(self) -> list[Diagnostic]:
        return self.by_severity(ERROR)

    def warnings(self) -> list[Diagnostic]:
        return self.by_severity(WARNING)

    # ------------------------------------------------------------------ counting
    def error_count(self, category: Optional[str] = None) -> int:
        return sum(1 for d in self._diagnostics
                   if d.severity == ERROR and (category is None or d.category == category))

    def warning_count(self, category: Optional[str] = None) -> int:
        return sum(1 for d in self._diagnostics
                   if d.severity == WARNING and (category is None or d.category == category))

    def has_errors(self) -> bool:
        return self.error_count() > 0

    def signature_counts(self) -> Counter:
        return Counter(d.signature for d in self._diagnostics if d.severity == ERROR)

    # ------------------------------------------------------------------ mutation
    def discard_where(self, predicate: Callable[[Diagnostic], bool]) -> list[Diagnostic]:
        """Remove diagnostics matching ``predicate`` and return them."""
        removed = [d for d in self._diagnostics if predicate(d)]
        if removed:
            self._diagnostics = [d for d in self._diagnostics if not predicate(d)]
            self._seen = {(d.error_id, d.line, d.column, d.message) for d in self._diagnostics}
        return removed

    def clear(self) -> None:
        self._diagnostics.clear()
        self._seen.clear()

    def __len__(self) -> int:
        return len(self._diagnostics)

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self._diagnostics)
