"""Code editor panel (syntax-highlighted via streamlit-ace when installed, plain text area otherwise)."""
from __future__ import annotations

from typing import Any

import streamlit as st

from ui.session import AppController

try:  # optional component
    from streamlit_ace import st_ace
    HAS_ACE = True
except ImportError:  # pragma: no cover - depends on the environment
    HAS_ACE = False


def editor_key(controller: AppController) -> str:
    return f"editor_{controller.state['editor_version']}"


def sync(controller: AppController) -> None:
    """Copy the editor widget's current text into the controller (call before any action)."""
    value = st.session_state.get(editor_key(controller))
    if isinstance(value, str):
        controller.set_source(value)


def run_action(controller: AppController, method: str, *args: Any, **kwargs: Any) -> None:
    """Button callback: sync the editor text first, then run a controller method."""
    sync(controller)
    getattr(controller, method)(*args, **kwargs)


def _annotations(controller: AppController) -> list[dict[str, Any]]:
    result = controller.result
    if result is None or controller.state.get("dirty"):
        return []
    kind = {"error": "error", "warning": "warning"}
    return [{"row": max(d.line - 1, 0), "column": max(d.column - 1, 0), "type": kind.get(d.severity, "info"),
             "text": f"{d.error_id}: {d.message}"} for d in result.diagnostics]


def render(controller: AppController) -> None:
    st.subheader("C source code")
    key = editor_key(controller)
    text = None
    if HAS_ACE:
        kwargs = dict(value=controller.source, language="c_cpp", theme="monokai", height=380, font_size=14,
                      tab_size=4, show_gutter=True, auto_update=True, key=key)
        try:
            text = st_ace(annotations=_annotations(controller), **kwargs)
        except TypeError:  # older streamlit-ace without the annotations argument
            text = st_ace(**kwargs)
    else:
        st.caption("Install `streamlit-ace` for syntax highlighting (pip install streamlit-ace).")
        text = st.text_area("Source", value=controller.source, height=380, key=key, label_visibility="collapsed")
    if isinstance(text, str):
        controller.set_source(text)

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    c1.button("Analyze code", type="primary", use_container_width=True, key="btn_analyze",
              on_click=run_action, args=(controller, "analyze"))
    c2.button("Undo last fix", use_container_width=True, key="btn_undo", disabled=not controller.fixer.can_undo,
              on_click=run_action, args=(controller, "undo"))
    c3.button("Reset to original", use_container_width=True, key="btn_reset",
              on_click=run_action, args=(controller, "reset"))
    c4.button("Clear editor", use_container_width=True, key="btn_clear",
              on_click=run_action, args=(controller, "clear"))
    if not HAS_ACE and controller.source.strip():
        with st.expander("Highlighted preview"):
            st.code(controller.source, language="c")
    if controller.state.get("dirty"):
        st.info("The code changed since the last analysis. Press **Analyze code** to refresh results.")
