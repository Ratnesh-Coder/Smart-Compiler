"""Source position helpers: offset <-> (line, column) conversion.

Lines and columns are 1-based. Offsets are 0-based indices into the source string.
Tabs count as a single column.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceLocation:
    """A position (and optional extent) inside a source text."""

    line: int
    column: int
    offset: int = -1
    length: int = 0

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


class LineIndex:
    """Pre-computed line start table for fast offset/line conversion."""

    def __init__(self, source: str) -> None:
        self.source = source
        starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                starts.append(i + 1)
        self._starts = starts

    @property
    def line_count(self) -> int:
        return len(self._starts)

    def locate(self, offset: int) -> tuple[int, int]:
        """Return the 1-based (line, column) of ``offset`` (clamped to the text)."""
        offset = max(0, min(offset, len(self.source)))
        idx = bisect_right(self._starts, offset) - 1
        return idx + 1, offset - self._starts[idx] + 1

    def line_start(self, line: int) -> int:
        line = max(1, min(line, self.line_count))
        return self._starts[line - 1]

    def line_end(self, line: int) -> int:
        """Offset of the newline that ends ``line`` (or the end of the text)."""
        line = max(1, min(line, self.line_count))
        if line < self.line_count:
            return self._starts[line] - 1
        return len(self.source)

    def offset_of(self, line: int, column: int = 1) -> int:
        start = self.line_start(line)
        return min(start + max(column, 1) - 1, self.line_end(line))

    def line_text(self, line: int) -> str:
        if line < 1 or line > self.line_count:
            return ""
        return self.source[self.line_start(line):self.line_end(line)]

    def indent_of(self, line: int) -> str:
        text = self.line_text(line)
        return text[: len(text) - len(text.lstrip(" \t"))]


def get_line_text(source: str, line: int) -> str:
    """Convenience wrapper returning one source line without its newline."""
    return LineIndex(source).line_text(line)
