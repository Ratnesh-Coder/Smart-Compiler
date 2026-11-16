"""Dashboard: page header, feature overview and the sidebar (samples, upload, limits)."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui.session import AppController

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_LABELS = {
    "valid_program.c": "Valid program (no errors)",
    "lexical_errors.c": "Lexical errors",
    "syntax_errors.c": "Syntax errors",
    "semantic_errors.c": "Semantic errors",
}


def list_samples() -> dict[str, Path]:
    return {SAMPLE_LABELS.get(p.name, p.name): p for p in sorted(SAMPLES_DIR.glob("*.c"))}


def render_header() -> None:
    st.title("Smart Compiler Error Detection and Correction System")
    st.markdown(
        "Detects **lexical**, **syntax** and **semantic** errors in C code with a real compiler pipeline "
        "(**PLY** lexer and parser, AST, symbol table, type checker) and proposes **context-aware, ranked "
        "corrections** that you can review, apply, undo and re-analyse."
    )
    cols = st.columns(4)
    cols[0].markdown("**Language analysed**  \nC (documented subset)")
    cols[1].markdown("**Implementation**  \nPython 3 + PLY")
    cols[2].markdown("**Detection**  \nLexical · Syntax · Semantic")
    cols[3].markdown("**Correction**  \nRules · Edit distance · Grammar · AST · Symbol table")
    with st.expander("Feature overview and supported C subset"):
        st.markdown(
            "- **Types:** `int float double char void`, arrays, string and character literals\n"
            "- **Statements:** `if/else`, `for`, `while`, `do-while`, `break`, `continue`, `return`, blocks\n"
            "- **Functions:** definitions, prototypes, calls, argument and return checking, `printf` / `scanf`\n"
            "- **Not supported (yet):** pointers, structs/unions, `switch`, `goto`, ternary and bitwise operators, "
            "casts, `sizeof`, preprocessor directives (ignored with a note). This is **not** a complete C compiler."
        )


def render_sidebar(controller: AppController) -> None:
    st.sidebar.header("Programs")
    samples = list_samples()
    if samples:
        choice = st.sidebar.selectbox("Sample program", list(samples), key="sample_choice")
        if st.sidebar.button("Load sample", use_container_width=True, key="load_sample_btn"):
            controller.load(samples[choice].read_text(encoding="utf-8"))
            st.rerun()
    uploaded = st.sidebar.file_uploader("...or upload a .c file", type=["c", "txt"], key="uploader")
    if uploaded is not None:
        marker = (uploaded.name, uploaded.size)
        if controller.state.get("last_upload") != marker:  # load once, not on every rerun
            controller.state["last_upload"] = marker
            controller.load(uploaded.getvalue().decode("utf-8", errors="replace"))
            st.rerun()
    st.sidebar.divider()
    st.sidebar.caption(
        "Confidence values are heuristic ranking scores, not calibrated probabilities. Corrections are "
        "never applied automatically."
    )
