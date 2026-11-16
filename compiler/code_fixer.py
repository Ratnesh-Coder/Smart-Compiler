"""Safe, position-based source modification.

Corrections are expressed as :class:`~compiler.error_manager.TextEdit` objects that refer to
absolute offsets in the exact source text that was analysed. The fixer therefore

* never performs global string replacement,
* refuses stale corrections (generated for a different version of the text),
* detects overlapping edits instead of silently producing garbage,
* never applies anything without an explicit call, and requires explicit confirmation for
  corrections that carry an *assumption* (i.e. may change program behaviour),
* keeps a history so every application can be undone, and the original is always preserved.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

from compiler.error_manager import Correction, TextEdit


class CodeFixerError(Exception):
    """Base class for fixer errors."""


class StaleCorrectionError(CodeFixerError):
    """The correction was generated for a different version of the source."""


class ConfirmationRequired(CodeFixerError):
    """The correction has an assumption that must be acknowledged first."""


class OverlappingEditError(CodeFixerError):
    """Edits overlap and cannot be applied together."""


def source_hash(source: str) -> str:
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def apply_edits(source: str, edits: Sequence[TextEdit]) -> str:
    """Apply non-overlapping ``edits`` (all relative to ``source``) in one pass.

    Raises :class:`OverlappingEditError` when ranges overlap and :class:`ValueError` when a
    range lies outside the text. Pure insertions at the same offset keep their given order.
    """
    ordered = sorted(enumerate(edits), key=lambda ie: (ie[1].start, ie[1].end, ie[0]))
    out: list[str] = []
    cursor = 0
    for _, e in ordered:
        if e.end > len(source):
            raise ValueError(f"Edit [{e.start}, {e.end}) lies outside the source (length {len(source)})")
        if e.start < cursor:
            raise OverlappingEditError(f"Edit at offset {e.start} overlaps a previous edit")
        out.append(source[cursor:e.start])
        out.append(e.replacement)
        cursor = e.end
    out.append(source[cursor:])
    return "".join(out)


def edits_conflict(a: Sequence[TextEdit], b: Sequence[TextEdit]) -> bool:
    """Do two corrections touch overlapping (or identical-start) regions?"""
    for x in a:
        for y in b:
            if x.start == y.start or (x.start < y.end and y.start < x.end):
                return True
    return False


@dataclass
class AppliedCorrection:
    correction: Correction
    before: str
    after: str


class CodeFixer:
    """Holds the working copy of the source and the correction history."""

    def __init__(self, source: str) -> None:
        self._original = source
        self._current = source
        self._history: list[AppliedCorrection] = []
        self._rejected: set[str] = set()

    # ------------------------------------------------------------------ state
    @property
    def original(self) -> str:
        return self._original

    @property
    def current(self) -> str:
        return self._current

    @property
    def history(self) -> list[AppliedCorrection]:
        return list(self._history)

    @property
    def applied_count(self) -> int:
        return len(self._history)

    @property
    def rejected_ids(self) -> set[str]:
        return set(self._rejected)

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    # ------------------------------------------------------------------ checks
    def _check(self, correction: Correction) -> None:
        if not correction.edits:
            raise CodeFixerError("This suggestion is advisory only and has no automatic edit.")
        if correction.source_hash and correction.source_hash != source_hash(self._current):
            raise StaleCorrectionError("The source changed since this correction was generated; re-analyse first.")

    # ------------------------------------------------------------------ operations
    def preview(self, correction: Correction) -> str:
        """Return the source as it would look after applying ``correction`` (nothing is committed)."""
        self._check(correction)
        return apply_edits(self._current, correction.edits)

    def apply(self, correction: Correction, confirmed: bool = False) -> str:
        """Apply one correction. Assumption-bearing corrections need ``confirmed=True``."""
        if correction.requires_confirmation and not confirmed:
            raise ConfirmationRequired(correction.assumption or "Confirmation required")
        new = self.preview(correction)
        self._history.append(AppliedCorrection(correction, self._current, new))
        self._current = new
        return new

    def apply_many(self, corrections: Iterable[Correction], confirmed_ids: Iterable[str] = ()
                   ) -> tuple[str, list[Correction], list[tuple[Correction, str]]]:
        """Apply several *independent* corrections generated for the same source version.

        Corrections that conflict with an earlier one, need unconfirmed assumptions or are stale
        are skipped and returned with a reason. The whole batch is a single undo step.
        """
        confirmed = set(confirmed_ids)
        accepted: list[Correction] = []
        skipped: list[tuple[Correction, str]] = []
        for c in corrections:
            try:
                self._check(c)
            except CodeFixerError as exc:
                skipped.append((c, str(exc)))
                continue
            if c.requires_confirmation and c.correction_id not in confirmed:
                skipped.append((c, "needs confirmation of its assumption"))
            elif any(edits_conflict(c.edits, a.edits) for a in accepted):
                skipped.append((c, "overlaps another selected correction"))
            else:
                accepted.append(c)
        if accepted:
            new = apply_edits(self._current, [e for c in accepted for e in c.edits])
            merged = Correction("batch", "; ".join(c.description for c in accepted),
                                [e for c in accepted for e in c.edits], min(c.confidence for c in accepted))
            self._history.append(AppliedCorrection(merged, self._current, new))
            self._current = new
        return self._current, accepted, skipped

    def reject(self, correction_id: str) -> None:
        self._rejected.add(correction_id)

    def is_rejected(self, correction_id: str) -> bool:
        return correction_id in self._rejected

    def undo(self) -> bool:
        """Revert the most recent application. Returns ``False`` if there is nothing to undo."""
        if not self._history:
            return False
        self._current = self._history.pop().before
        return True

    def set_source(self, source: str) -> None:
        """Replace the working copy by text edited manually (history stays; the text is the new base)."""
        self._current = source

    def reset(self) -> None:
        """Discard all corrections and return to the original source."""
        self._current = self._original
        self._history.clear()
        self._rejected.clear()
