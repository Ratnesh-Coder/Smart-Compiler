"""Input validation and normalisation helpers."""
from __future__ import annotations

import re

MAX_SOURCE_CHARS = 200_000
C_KEYWORDS = frozenset({"int", "float", "double", "char", "void", "if", "else", "for",
                        "while", "do", "break", "continue", "return"})
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_source(source: str) -> str:
    """Normalise line endings to ``\\n`` and strip a leading BOM."""
    if source.startswith("\ufeff"):
        source = source[1:]
    return source.replace("\r\n", "\n").replace("\r", "\n")


def validate_source(source: object) -> tuple[bool, str]:
    """Check that ``source`` is analysable. Returns ``(ok, message)``."""
    if not isinstance(source, str):
        return False, "Source code must be text."
    if "\x00" in source:
        return False, "Source code contains NUL bytes (is this a binary file?)."
    if len(source) > MAX_SOURCE_CHARS:
        return False, f"Source code is too large ({len(source)} > {MAX_SOURCE_CHARS} characters)."
    if not source.strip():
        return False, "Source code is empty."
    return True, ""


def is_valid_identifier(name: str) -> bool:
    return bool(_IDENT_RE.match(name)) and name not in C_KEYWORDS
