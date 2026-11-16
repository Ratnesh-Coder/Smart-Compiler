"""Original-versus-modified source comparison helpers (built on difflib)."""
from __future__ import annotations

import difflib
import html
from dataclasses import dataclass


@dataclass
class DiffRow:
    """One row of a side-by-side comparison."""

    left_no: int | None
    left_text: str
    right_no: int | None
    right_text: str
    tag: str  # 'equal' | 'replace' | 'delete' | 'insert'


def unified_diff(original: str, modified: str, from_name: str = "original.c",
                 to_name: str = "corrected.c") -> str:
    lines = difflib.unified_diff(original.splitlines(), modified.splitlines(),
                                 fromfile=from_name, tofile=to_name, lineterm="")
    return "\n".join(lines)


def side_by_side_rows(original: str, modified: str) -> list[DiffRow]:
    """Align two sources line by line using :class:`difflib.SequenceMatcher`."""
    a, b = original.splitlines(), modified.splitlines()
    rows: list[DiffRow] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append(DiffRow(i1 + k + 1, a[i1 + k], j1 + k + 1, b[j1 + k], "equal"))
            continue
        span = max(i2 - i1, j2 - j1)
        for k in range(span):
            li, rj = i1 + k, j1 + k
            has_l, has_r = li < i2, rj < j2
            rows.append(DiffRow(li + 1 if has_l else None, a[li] if has_l else "",
                                rj + 1 if has_r else None, b[rj] if has_r else "",
                                tag if (has_l and has_r) else ("delete" if has_l else "insert")))
    return rows


def changed_line_numbers(original: str, modified: str) -> list[int]:
    """1-based line numbers (in ``modified``) that differ from ``original``."""
    return [r.right_no for r in side_by_side_rows(original, modified)
            if r.tag != "equal" and r.right_no is not None]


def side_by_side_html(original: str, modified: str) -> str:
    """Render the comparison as a self-contained HTML table."""
    colors = {"equal": "transparent", "replace": "#fff3b0", "delete": "#ffd6d6", "insert": "#d4f7d4"}
    out = ['<table style="width:100%;border-collapse:collapse;font-family:monospace;font-size:13px">',
           '<tr><th style="text-align:left;width:50%">Original</th>'
           '<th style="text-align:left;width:50%">Corrected</th></tr>']
    for row in side_by_side_rows(original, modified):
        bg = colors[row.tag]
        left = f"{row.left_no or ''}".rjust(3) + "  " + html.escape(row.left_text)
        right = f"{row.right_no or ''}".rjust(3) + "  " + html.escape(row.right_text)
        out.append(f'<tr style="background:{bg}"><td><pre style="margin:0">{left}</pre></td>'
                   f'<td><pre style="margin:0">{right}</pre></td></tr>')
    out.append("</table>")
    return "\n".join(out)
