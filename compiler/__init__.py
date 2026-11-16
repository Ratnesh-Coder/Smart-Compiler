"""Smart Compiler Error Detection and Correction System - compiler package.

Heavy, PLY-dependent modules are exported lazily so that PLY-independent modules
(symbol table, type checker, semantic analyzer, correction engine, code fixer ...) can be
imported and tested even when PLY is not installed.
"""
from __future__ import annotations

import importlib
from typing import Any

__all__ = ["CompilerPipeline", "AnalysisResult", "Diagnostic", "Correction", "TextEdit",
           "ErrorManager", "CodeFixer"]

_LAZY = {
    "CompilerPipeline": "compiler.compiler_pipeline",
    "AnalysisResult": "compiler.compiler_pipeline",
    "Diagnostic": "compiler.error_manager",
    "Correction": "compiler.error_manager",
    "TextEdit": "compiler.error_manager",
    "ErrorManager": "compiler.error_manager",
    "CodeFixer": "compiler.code_fixer",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module 'compiler' has no attribute {name!r}")
