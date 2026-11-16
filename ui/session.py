"""UI state controller (framework independent).

All state transitions of the application - editing, analysing, applying / rejecting / undoing
corrections - are implemented here on top of a plain ``MutableMapping``. In the app this mapping
is ``st.session_state``; in tests it is a ``dict``. Keeping the logic out of the Streamlit widgets
guarantees that script reruns cannot silently discard user code or correction decisions.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from collections.abc import MutableMapping

from compiler.code_fixer import CodeFixer, CodeFixerError, ConfirmationRequired
from compiler.error_manager import Correction, Diagnostic


class AnalyzerLike(Protocol):
    def analyze(self, source: str) -> Any: ...


class AppController:
    """Owns the session state of one user session."""

    def __init__(self, state: MutableMapping, pipeline: AnalyzerLike) -> None:
        self.state = state
        self.pipeline = pipeline
        self.init()

    # ------------------------------------------------------------------ state
    def init(self, default_source: str = "") -> None:
        s = self.state
        s.setdefault("source", default_source)
        s.setdefault("fixer", CodeFixer(s["source"]))
        s.setdefault("result", None)
        s.setdefault("editor_version", 0)
        s.setdefault("baseline", None)        # error/warning counts of the first analysis
        s.setdefault("messages", [])          # (level, text) feedback shown once
        s.setdefault("dirty", False)          # source edited since last analysis

    @property
    def fixer(self) -> CodeFixer:
        return self.state["fixer"]

    @property
    def result(self) -> Any:
        return self.state["result"]

    @property
    def source(self) -> str:
        return self.state["source"]

    def _notify(self, level: str, text: str) -> None:
        self.state["messages"].append((level, text))

    def pop_messages(self) -> list[tuple[str, str]]:
        msgs, self.state["messages"] = list(self.state["messages"]), []
        return msgs

    def _push_to_editor(self, text: str) -> None:
        self.state["source"] = text
        self.state["editor_version"] += 1  # forces the editor widget to display the new text

    # ------------------------------------------------------------------ editing
    def set_source(self, text: str) -> None:
        """Called when the user edits the code manually."""
        if text != self.state["source"]:
            self.state["source"] = text
            self.fixer.set_source(text)
            self.state["dirty"] = True

    def load(self, text: str) -> None:
        """Load a new program (sample or upload) and start a fresh session for it."""
        self.state["fixer"] = CodeFixer(text)
        self.state["result"] = None
        self.state["baseline"] = None
        self.state["dirty"] = False
        self._push_to_editor(text)

    def clear(self) -> None:
        self.load("")

    # ------------------------------------------------------------------ analysis
    def analyze(self) -> Any:
        """Run the compiler pipeline on the current source."""
        text = self.state["source"]
        self.fixer.set_source(text)
        result = self.pipeline.analyze(text)
        self.state["result"] = result
        self.state["dirty"] = False
        if self.state["baseline"] is None:
            self.state["baseline"] = {"errors": result.error_count(), "warnings": result.warning_count(),
                                      "original": text}
        return result

    # ------------------------------------------------------------------ corrections
    def find_correction(self, correction_id: str) -> Optional[tuple[Diagnostic, Correction]]:
        result = self.result
        if result is None:
            return None
        for d in result.diagnostics:
            for c in d.corrections:
                if c.correction_id == correction_id:
                    return d, c
        return None

    def apply_correction(self, correction_id: str, confirmed: bool = False) -> bool:
        """Apply one correction after explicit user approval, then re-analyse automatically."""
        found = self.find_correction(correction_id)
        if found is None:
            self._notify("error", "That suggestion is no longer available - please analyse again.")
            return False
        if self.state["dirty"]:
            self._notify("warning", "The code was edited after the last analysis. Analyse again before applying suggestions.")
            return False
        _, correction = found
        try:
            new_source = self.fixer.apply(correction, confirmed=confirmed)
        except ConfirmationRequired as exc:
            self._notify("warning", f"Please confirm the assumption first: {exc}")
            return False
        except CodeFixerError as exc:
            self._notify("error", str(exc))
            return False
        self._push_to_editor(new_source)
        self.analyze()
        self._notify("success", f"Applied: {correction.description}")
        return True

    def apply_all_safe(self) -> int:
        """Apply the best *assumption-free* correction of every error in one undoable step.

        Only independent corrections are combined; overlapping ones are skipped (they are offered
        again after the automatic re-analysis).
        """
        result = self.result
        if result is None or self.state["dirty"]:
            self._notify("warning", "Analyse the current code first.")
            return 0
        picks = []
        for d in result.diagnostics:
            if d.severity != "error":
                continue
            best = next((c for c in self.visible_corrections(d) if c.edits and not c.requires_confirmation), None)
            if best is not None:
                picks.append(best)
        if not picks:
            self._notify("info", "No assumption-free corrections are available.")
            return 0
        new_source, accepted, skipped = self.fixer.apply_many(picks)
        if not accepted:
            self._notify("warning", "None of the corrections could be applied (" + "; ".join(r for _, r in skipped) + ").")
            return 0
        self._push_to_editor(new_source)
        self.analyze()
        extra = f" ({len(skipped)} skipped: overlapping or stale)" if skipped else ""
        self._notify("success", f"Applied {len(accepted)} correction(s){extra}.")
        return len(accepted)

    def reject_correction(self, correction_id: str) -> None:
        self.fixer.reject(correction_id)
        self._notify("info", "Suggestion rejected (it stays hidden until you re-analyse a changed program).")

    def undo(self) -> bool:
        if not self.fixer.undo():
            self._notify("info", "Nothing to undo.")
            return False
        self._push_to_editor(self.fixer.current)
        self.analyze()
        self._notify("info", "Last correction undone.")
        return True

    def reset(self) -> None:
        """Return to the original program, discarding every correction."""
        original = self.fixer.original
        self.fixer.reset()
        self.state["baseline"] = None
        self._push_to_editor(original)
        self.state["result"] = None
        self.state["dirty"] = False

    def visible_corrections(self, diagnostic: Diagnostic) -> list[Correction]:
        return [c for c in diagnostic.corrections if not self.fixer.is_rejected(c.correction_id)]

    # ------------------------------------------------------------------ report
    def final_report(self) -> dict[str, Any]:
        result, base = self.result, self.state["baseline"]
        return {
            "analysed": result is not None,
            "errors_detected": base["errors"] if base else 0,
            "warnings_detected": base["warnings"] if base else 0,
            "corrections_applied": self.fixer.applied_count,
            "corrections_rejected": len(self.fixer.rejected_ids),
            "remaining_errors": result.error_count() if result else 0,
            "remaining_warnings": result.warning_count() if result else 0,
            "status": result.status if result else "Not analysed",
            "complete": bool(result and result.complete),
            "passed": bool(result and result.passed),
        }
