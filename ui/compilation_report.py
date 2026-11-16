"""Final compilation report and compiler internals (tokens, AST, symbol table)."""
from __future__ import annotations

import json

import streamlit as st

from compiler.ast_nodes import format_tree
from ui.session import AppController


def render_report(controller: AppController) -> None:
    rep = controller.final_report()
    if not rep["analysed"]:
        st.info("Analyse the code to produce a report.")
        return
    c = st.columns(5)
    c[0].metric("Errors detected (first analysis)", rep["errors_detected"])
    c[1].metric("Warnings (first analysis)", rep["warnings_detected"])
    c[2].metric("Corrections applied", rep["corrections_applied"])
    c[3].metric("Remaining errors", rep["remaining_errors"])
    c[4].metric("Remaining warnings", rep["remaining_warnings"])
    st.markdown(f"**Analysis status:** {rep['status']}  \n**Analysis complete:** {'yes' if rep['complete'] else 'no'}  \n"
                f"**Corrections rejected:** {rep['corrections_rejected']}")
    if rep["passed"]:
        st.success("The program passed ALL implemented compiler checks (lexical, syntax, semantic).")
    else:
        st.error("The program did not pass all implemented compiler checks.")
    st.caption("'Passed' means: no errors from this tool's checks for the supported C subset. It does not "
               "guarantee that a full C compiler would accept the program or that it behaves correctly.")
    result = controller.result
    st.markdown("**Phase status**")
    st.table([{"Phase": p.name, "Status": p.status, "Details": p.note} for p in result.phases])
    if result.skipped_checks:
        st.warning("Skipped checks: " + ", ".join(result.skipped_checks))
    st.download_button("Download analysis report (JSON)", json.dumps(result.to_dict(), indent=2),
                       file_name="analysis_report.json", mime="application/json", key="dl_report")


def render_internals(controller: AppController) -> None:
    result = controller.result
    if result is None:
        st.info("Analyse the code first.")
        return
    t1, t2, t3 = st.tabs(["Token stream", "Abstract syntax tree", "Symbol table"])
    with t1:
        st.dataframe([{"Type": t.type, "Lexeme": t.value, "Line": t.line, "Column": t.column,
                       "Malformed": t.malformed} for t in result.tokens], use_container_width=True)
    with t2:
        if result.ast is None:
            st.info("No syntax tree is available.")
        else:
            st.code(format_tree(result.ast), language="text")
    with t3:
        if result.symbol_table is None:
            st.info("Symbol table not built (semantic analysis was skipped).")
        else:
            st.dataframe(result.symbol_table.rows(), use_container_width=True)
