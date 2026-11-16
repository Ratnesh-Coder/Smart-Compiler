"""Error analysis dashboard: counters, status and per-diagnostic details."""
from __future__ import annotations

import streamlit as st

from compiler.error_manager import Diagnostic

CATEGORY_BADGE = {"Lexical": ":violet[Lexical]", "Syntax": ":orange[Syntax]", "Semantic": ":blue[Semantic]"}
SEVERITY_ICON = {"error": "🔴", "warning": "🟡", "suggestion": "💡"}
CATEGORY_ICON = {"Lexical": "🟣", "Syntax": "🟠", "Semantic": "🔵"}


def render_summary(result) -> None:
    s = result.summary()
    cols = st.columns(6)
    cols[0].metric("Total errors", s["errors"])
    cols[1].metric("🟣 Lexical", s["lexical"])
    cols[2].metric("🟠 Syntax", s["syntax"])
    cols[3].metric("🔵 Semantic", s["semantic"])
    cols[4].metric("🟡 Warnings", s["warnings"])
    cols[5].metric("Analysis status", s["status"])
    status = s["status"]
    if status == "Passed":
        st.success("No errors or warnings were found by the implemented compiler checks.")
    elif status == "Passed with warnings":
        st.warning("No errors, but there are warnings.")
    elif status == "Incomplete":
        st.warning("Analysis incomplete: " + "; ".join(result.skipped_checks or ["see notes"]))
    elif status == "Internal error":
        st.error(result.internal_error)
    else:
        st.error("Errors were found.")
    for note in result.notes:
        st.caption("ℹ️ " + note)
    if result.skipped_checks and status != "Incomplete":
        st.caption("⏭️ Skipped checks: " + ", ".join(result.skipped_checks))


def _caret(d: Diagnostic) -> str:
    pad = " " * max(d.column - 1, 0)
    return f"{d.offending_code}\n{pad}{'^' * max(d.length, 1)}"


def render_diagnostic(d: Diagnostic) -> None:
    title = (f"{SEVERITY_ICON.get(d.severity, '')} {CATEGORY_ICON.get(d.category, '')} {d.error_id} · "
             f"line {d.line}, col {d.column} · {d.message}")
    with st.expander(title):
        c = st.columns(4)
        c[0].markdown(f"**Error ID**  \n`{d.error_id}`")
        c[1].markdown(f"**Category**  \n{CATEGORY_BADGE.get(d.category, d.category)}")
        c[2].markdown(f"**Severity**  \n{d.severity.capitalize()}")
        c[3].markdown(f"**Location**  \nline {d.line}, column {d.column}")
        st.markdown(f"**Message:** {d.message}")
        st.markdown(f"**Explanation:** {d.description}")
        st.markdown("**Offending source code**")
        st.code(_caret(d), language="c")
        if d.suggestion:
            st.markdown(f"**Suggested correction:** {d.suggestion}")
        if d.corrected_code:
            st.markdown("**Corrected code (preview)**")
            st.code(d.corrected_code, language="c")
        if d.confidence is not None:
            st.progress(min(max(d.confidence, 0.0), 1.0), text=f"Heuristic confidence {d.confidence:.0%} (ranking score, not a probability)")


def render(result) -> None:
    render_summary(result)
    if not result.diagnostics:
        return
    f1, f2 = st.columns(2)
    cats = f1.multiselect("Category", ["Lexical", "Syntax", "Semantic"], default=["Lexical", "Syntax", "Semantic"],
                          key="filter_cat")
    sevs = f2.multiselect("Severity", ["error", "warning", "suggestion"], default=["error", "warning", "suggestion"],
                          key="filter_sev")
    shown = [d for d in result.diagnostics if d.category in cats and d.severity in sevs]
    st.caption(f"Showing {len(shown)} of {len(result.diagnostics)} diagnostics")
    for d in shown:
        render_diagnostic(d)
