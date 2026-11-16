"""Smart Compiler Error Detection and Correction System - Streamlit entry point.

Run from the project root:   streamlit run app.py
This file only wires UI widgets to the compiler pipeline; it contains no compiler logic.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="Smart Compiler Error Detection and Correction", page_icon="🛠️", layout="wide")

from ui import code_editor, compilation_report, correction_panel, dashboard, error_panel  # noqa: E402
from ui.session import AppController  # noqa: E402


@st.cache_resource(show_spinner="Building the compiler (generating LALR tables)...")
def get_pipeline():
    from compiler.compiler_pipeline import CompilerPipeline
    return CompilerPipeline()


def main() -> None:
    try:
        pipeline = get_pipeline()
    except ImportError as exc:
        st.error(f"A required dependency is missing: {exc}\n\nRun `pip install -r requirements.txt` and restart.")
        st.stop()
        return

    controller = AppController(st.session_state, pipeline)
    dashboard.render_header()
    dashboard.render_sidebar(controller)

    for level, text in controller.pop_messages():
        getattr(st, level if level in ("success", "info", "warning", "error") else "info")(text)

    code_editor.render(controller)

    tab_errors, tab_fix, tab_report, tab_internals = st.tabs(
        ["Error analysis", "Correction workspace", "Final compilation report", "Compiler internals"])
    with tab_errors:
        if controller.result is None:
            st.info("Press **Analyze code** to run the compiler pipeline.")
        else:
            error_panel.render(controller.result)
    with tab_fix:
        correction_panel.render(controller)
    with tab_report:
        compilation_report.render_report(controller)
    with tab_internals:
        compilation_report.render_internals(controller)


main()
