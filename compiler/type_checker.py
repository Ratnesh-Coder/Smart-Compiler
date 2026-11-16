"""Type rules for the supported C subset (pure functions, no AST dependency).

Types are strings: ``char int float double void``; arrays append ``[]`` (``int[]``);
the address of a scalar (only legal as a ``scanf`` argument) appends ``*`` (``int*``).
``ERROR_TYPE`` marks an expression whose error was already reported; it is compatible with
everything so that one mistake does not cascade into many diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ERROR_TYPE = "<error>"
STRING_TYPE = "char[]"
NUMERIC_TYPES = ("char", "int", "float", "double")
_RANK = {"char": 1, "int": 2, "float": 3, "double": 4}
_LABELS = {"int": "an integer", "char": "a character", "float": "a floating-point",
           "double": "a double-precision floating-point"}


@dataclass
class TypeCheckResult:
    ok: bool
    warning: bool = False
    kind: str = ""      # '', 'float_to_int', 'double_to_float', 'void_value', 'string', 'array', 'pointer', 'other'
    reason: str = ""


def is_array(t: str) -> bool:
    return t.endswith("[]")


def is_pointer(t: str) -> bool:
    return t.endswith("*")


def element_type(t: str) -> str:
    return t[:-2] if is_array(t) else t


def array_of(t: str) -> str:
    return t + "[]"


def is_numeric(t: str) -> bool:
    return t in _RANK


def is_integral(t: str) -> bool:
    return t in ("char", "int")


def promote(t: str) -> str:
    return "int" if t == "char" else t


def common_type(a: str, b: str) -> str:
    """Usual arithmetic conversions."""
    return promote(a) if _RANK[a] >= _RANK[b] else promote(b)


def describe_type(t: str) -> str:
    if t == STRING_TYPE:
        return "string (char array)"
    if is_array(t):
        return f"{t[:-2]} array"
    if is_pointer(t):
        return f"address of {t[:-1]}"
    return t


def variable_label(t: str) -> str:
    """'an integer' for 'int' etc. (used in messages such as 'assign a string to an integer variable')."""
    return _LABELS.get(t, f"a '{t}'")


def check_assignment(target: str, source: str, source_is_string_literal: bool = False) -> TypeCheckResult:
    """Can a value of type ``source`` be stored in ``target``?"""
    if target == ERROR_TYPE or source == ERROR_TYPE:
        return TypeCheckResult(True)
    if source == "void":
        return TypeCheckResult(False, kind="void_value", reason="A void expression has no value.")
    if is_array(target):
        if is_array(source) and element_type(source) == element_type(target):
            return TypeCheckResult(True)
        return TypeCheckResult(False, kind="array",
                               reason=f"Cannot assign {describe_type(source)} to {describe_type(target)}.")
    if is_numeric(target):
        if is_numeric(source):
            if source in ("float", "double") and target in ("int", "char"):
                return TypeCheckResult(True, True, "float_to_int",
                                       f"Converting '{source}' to '{target}' discards the fractional part.")
            if source == "double" and target == "float":
                return TypeCheckResult(True, True, "double_to_float",
                                       "Converting 'double' to 'float' may lose precision.")
            return TypeCheckResult(True)
        if source_is_string_literal or source == STRING_TYPE:
            return TypeCheckResult(False, kind="string",
                                   reason=f"Cannot assign a string literal to {variable_label(target)} variable.")
        return TypeCheckResult(False, kind="array" if is_array(source) else "pointer",
                               reason=f"Cannot assign {describe_type(source)} to a variable of type '{target}'.")
    return TypeCheckResult(False, kind="other", reason=f"Cannot assign to type '{target}'.")


def binary_result(op: str, left: str, right: str) -> tuple[Optional[str], str]:
    """Result type of ``left op right`` or ``(None, reason)`` when the operands are incompatible."""
    if left == ERROR_TYPE or right == ERROR_TYPE:
        return ERROR_TYPE, ""
    for t in (left, right):
        if t == "void":
            return None, "A void value cannot be used as an operand."
        if not is_numeric(t):
            return None, f"Operator '{op}' cannot be applied to {describe_type(t)}."
    if op == "%":
        if not (is_integral(left) and is_integral(right)):
            return None, "Operator '%' requires integer operands."
        return "int", ""
    if op in ("+", "-", "*", "/"):
        return common_type(left, right), ""
    return "int", ""  # relational, equality, logical


def unary_result(op: str, operand: str) -> tuple[Optional[str], str]:
    if operand == ERROR_TYPE:
        return ERROR_TYPE, ""
    if operand == "void":
        return None, "A void value cannot be used as an operand."
    if not is_numeric(operand):
        return None, f"Operator '{op}' cannot be applied to {describe_type(operand)}."
    if op == "!":
        return "int", ""
    if op in ("++", "--"):
        return operand, ""
    return promote(operand), ""
