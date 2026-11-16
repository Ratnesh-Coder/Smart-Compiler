"""Symbol table with stack-based nested scopes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

VARIABLE = "variable"
ARRAY = "array"
FUNCTION = "function"
PARAMETER = "parameter"


@dataclass
class ParamInfo:
    type_name: str
    name: Optional[str] = None
    is_array: bool = False

    @property
    def value_type(self) -> str:
        return self.type_name + "[]" if self.is_array else self.type_name


@dataclass
class Symbol:
    """One entry of the symbol table."""

    name: str
    type_name: str                      # variable/element type, or return type for functions
    category: str
    scope_name: str = "global"
    scope_level: int = 0
    line: int = 0
    column: int = 0
    offset: int = -1                    # offset of the declared name
    type_offset: int = -1               # offset/length of the type keyword in the declaration
    type_length: int = 0
    params: Optional[list[ParamInfo]] = None
    is_defined: bool = True             # functions: False for a bare prototype
    variadic: bool = False
    is_builtin: bool = False
    is_array: bool = False
    array_size: Optional[int] = None
    initialized: bool = False

    @property
    def value_type(self) -> str:
        if self.category == FUNCTION:
            return self.type_name
        return self.type_name + "[]" if (self.is_array or self.category == ARRAY) else self.type_name

    def signature(self) -> tuple[str, tuple[str, ...]]:
        return self.type_name, tuple(p.value_type for p in (self.params or []))

    def brief(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.value_type, "category": self.category,
                "params": [p.value_type for p in self.params] if self.params is not None else None}


@dataclass
class Scope:
    name: str
    level: int
    parent: Optional["Scope"] = None
    symbols: dict[str, Symbol] = field(default_factory=dict)


class SymbolTable:
    """Stack of scopes; closed scopes are remembered so scope violations can be explained."""

    def __init__(self) -> None:
        self.global_scope = Scope("global", 0)
        self.current = self.global_scope
        self._closed: list[Scope] = []

    # ------------------------------------------------------------------ scope management
    def enter_scope(self, name: str = "block") -> Scope:
        self.current = Scope(name, self.current.level + 1, self.current)
        return self.current

    def exit_scope(self) -> None:
        if self.current.parent is None:
            raise RuntimeError("Cannot exit the global scope")
        self._closed.append(self.current)
        self.current = self.current.parent

    @property
    def depth(self) -> int:
        return self.current.level

    # ------------------------------------------------------------------ declarations
    def declare(self, symbol: Symbol) -> Optional[Symbol]:
        """Declare ``symbol`` in the current scope.

        Returns the already existing symbol on a duplicate (nothing is overwritten), otherwise ``None``.
        """
        existing = self.current.symbols.get(symbol.name)
        if existing is not None:
            return existing
        symbol.scope_name = self.current.name
        symbol.scope_level = self.current.level
        self.current.symbols[symbol.name] = symbol
        return None

    def lookup(self, name: str) -> Optional[Symbol]:
        scope: Optional[Scope] = self.current
        while scope is not None:
            if name in scope.symbols:
                return scope.symbols[name]
            scope = scope.parent
        return None

    def lookup_local(self, name: str) -> Optional[Symbol]:
        return self.current.symbols.get(name)

    def lookup_global(self, name: str) -> Optional[Symbol]:
        return self.global_scope.symbols.get(name)

    def find_in_closed_scopes(self, name: str) -> Optional[Symbol]:
        for scope in reversed(self._closed):
            if name in scope.symbols:
                return scope.symbols[name]
        return None

    def visible_symbols(self) -> dict[str, Symbol]:
        chain: list[Scope] = []
        scope: Optional[Scope] = self.current
        while scope is not None:
            chain.append(scope)
            scope = scope.parent
        visible: dict[str, Symbol] = {}
        for sc in reversed(chain):  # outer first so inner scopes shadow
            visible.update(sc.symbols)
        return visible

    # ------------------------------------------------------------------ reporting
    def all_symbols(self) -> list[Symbol]:
        result = list(self.global_scope.symbols.values())
        for scope in self._closed:
            result.extend(scope.symbols.values())
        return result

    def rows(self) -> list[dict[str, Any]]:
        """Tabular view used by the UI."""
        out = []
        for s in self.all_symbols():
            out.append({
                "Name": s.name, "Type": s.value_type, "Category": s.category,
                "Scope": s.scope_name, "Level": s.scope_level, "Line": s.line or "-",
                "Details": (f"({', '.join(p.value_type for p in s.params)})" if s.params is not None
                            else (f"size {s.array_size}" if s.array_size is not None else "")),
            })
        return out
