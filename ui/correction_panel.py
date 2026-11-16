"""Correction workspace: review, apply or reject suggestions and compare original/corrected code."""
from __future__ import annotations

import streamlit as st

from compiler.error_manager import Correction, Diagnostic
from ui.code_editor import run_action
from ui.session import AppController
from utils.code_diff import side_by_side_html, unified_diff


def _apply(controller: AppController, correction_id: str) -> None:
    confirmed = bool(st.session_state.get(f"confirm_{correction_id}", False))
    run_action(controller, "apply_correction", correction_id, confirmed=confirmed)


def _reject(controller: AppController, correction_id: str) -> None:
    run_action(controller, "reject_correction", correction_id)


def _render_correction(controller: AppController, c: Correction, rank: int) -> None:
    label = "Suggested correction" if rank == 0 else f"Alternative {rank}"
    st.markdown(f"**{label}:** {c.description}")
    st.caption(f"{c.explanation}  \nMethod: `{c.method}` · heuristic confidence {c.confidence:.0%} "
               f"(ranking score, not a calibrated probability)")
    if c.score_breakdown:
        b = c.score_breakdown
        st.caption(f"Score components - grammar {b['grammar']:.2f}, context {b['context']:.2f}, "
                   f"symbol {b['symbol']:.2f}, similarity {b['similarity']:.2f}")
    if c.preview:
        st.code(c.preview, language="c")
    if c.assumption:
        st.warning(f"Assumption: {c.assumption}")
        st.checkbox("I understand and accept this assumption", key=f"confirm_{c.correction_id}")
    if not c.edits:
        st.info("Advisory only - this suggestion has no automatic edit.")
    b1, b2, _ = st.columns([1, 1, 3])
    b1.button("Apply correction", key=f"apply_{c.correction_id}", type="primary" if rank == 0 else "secondary",
              disabled=not c.edits, on_click=_apply, args=(controller, c.correction_id))
    b2.button("Reject", key=f"reject_{c.correction_id}", on_click=_reject, args=(controller, c.correction_id))


def _render_diagnostic(controller: AppController, d: Diagnostic) -> None:
    options = controller.visible_corrections(d)
    icon = "🔴" if d.severity == "error" else "🟡"
    with st.expander(f"{icon} {d.error_id} · line {d.line} · {d.message}", expanded=d.severity == "error"):
        if not options:
            hidden = len(d.corrections) - len(options)
            st.write("No suggestions available." if not d.corrections else f"All {hidden} suggestion(s) were rejected.")
            if d.suggestion:
                st.markdown(f"**Hint:** {d.suggestion}")
            return
        for rank, c in enumerate(options):
            if rank:
                st.divider()
            _render_correction(controller, c, rank)


def render(controller: AppController) -> None:
    result = controller.result
    if result is None:
        st.info("Analyse the code first to see correction suggestions.")
        return
    if controller.state.get("dirty"):
        st.warning("The code changed after the last analysis; suggestions refer to the analysed version. "
                   "Press **Analyze code** again.")
    items = [d for d in result.diagnostics if d.severity != "suggestion"]
    if not items:
        st.success("Nothing to correct.")
    else:
        top = st.columns([1, 3])
        top[0].button("Apply all safe fixes", key="btn_apply_all", on_click=run_action,
                      args=(controller, "apply_all_safe"),
                      help="Applies the best assumption-free correction of every error in one undoable step.")
        top[1].caption("Corrections that carry an assumption are never included; you must confirm them one by one.")
        for d in items:
            _render_diagnostic(controller, d)

    st.divider()
    st.subheader("Original versus corrected code")
    original, current = controller.fixer.original, controller.fixer.current
    if original == current:
        st.caption("No modifications yet - the corrected code equals the original.")
    tab_side, tab_diff, tab_code = st.tabs(["Side by side", "Unified diff", "Corrected code"])
    with tab_side:
        st.markdown(side_by_side_html(original, current), unsafe_allow_html=True)
    with tab_diff:
        st.code(unified_diff(original, current) or "(no differences)", language="diff")
    with tab_code:
        st.code(current, language="c")
        st.download_button("Download corrected .c file", current, file_name="corrected.c", key="dl_corrected")
    if controller.fixer.history:
        st.markdown("**Applied corrections**")
        for i, h in enumerate(controller.fixer.history, 1):
            st.write(f"{i}. {h.correction.description}")
