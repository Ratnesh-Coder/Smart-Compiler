# Smart Compiler Error Detection and Correction System

*Detecting Error and Suggesting Meaningful Correction* — a Compiler Design project that analyses a
documented **subset of C** through a real compiler front end (**PLY** lexer + LALR parser, AST, symbol
table, type checker), classifies **lexical / syntax / semantic** errors, and proposes **ranked,
context-aware corrections** that the user can review, apply, reject, undo and re-analyse.

> **Verification status — please read.** This project was built in an environment where PLY, Streamlit and
> pytest could **not** be installed. The PLY-dependent code (lexer, grammar, parser, pipeline) and the
> Streamlit UI have therefore **never been executed against the real libraries**. See
> [Verification status](#verification-status) for exactly what was and was not tested.

## Features

| Area | What it does |
|---|---|
| Lexical analysis (PLY Lex) | tokens with exact line/column; invalid characters, malformed numbers, unterminated strings / comments, bad char literals are reported, never silently dropped |
| Syntax analysis (PLY Yacc) | LALR(1) grammar with precedence/associativity, AST, **token-insertion repair** (one precise diagnostic per real mistake, no cascades), panic-mode fallback |
| Semantic analysis | stack-based symbol table, scope checking, type checker with valid implicit conversions, call/return/array checks, `printf`/`scanf` format checks |
| Correction engine | rule-based, Levenshtein / Damerau edit distance, grammar-based, AST-based and symbol-table-based candidates; **verified by re-analysis**, weighted ranking, explanations |
| Safe code fixer | offset-based edits, overlap and stale-edit detection, explicit confirmation for corrections with assumptions, undo, original always preserved |
| Streamlit UI | editor (Ace highlighting), error dashboard, correction workspace, side-by-side diff, final compilation report |

Error identifiers: `LEX001–LEX006`, `SYN001–SYN010`, `SEM001–SEM022` (full table in
[docs/user_guide.md](docs/user_guide.md#error-catalogue)).

## Supported C subset

* Types: `int float double char void`; one-dimensional arrays; string and character literals
* Declarations (multiple declarators, initialisers, array initialiser lists), global and local variables
* Operators: arithmetic, relational, logical, assignment (`= += -= *= /= %=`), `++`/`--`, unary `- + ! &`
* Statements: expression, `if`/`else` (nested, dangling-else), `for`, `while`, `do-while`, `break`, `continue`, `return`, blocks / nested scopes
* Functions: prototypes, definitions, calls, parameters (including array parameters), `printf`, `scanf` (built in)
* Comments (`//`, `/* */`); preprocessor lines (e.g. `#include`) are **ignored** with an informational note

**Not supported** (documented future work): pointers (only `&x` as a `scanf` argument), `struct`/`union`/`enum`,
`switch`, `goto`, ternary `?:`, bitwise operators, casts, `sizeof`, multi-dimensional arrays, `const`/`static`,
string concatenation, preprocessor macros, a real C library. This is **not** a complete or ISO-compliant C compiler,
and "passed" only means "passed the checks implemented here".

## Installation (Windows)

Requires **Python 3.10 or newer** (developed against 3.12). In *Command Prompt* or *PowerShell*:

```bat
cd smart_compiler
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

PowerShell may refuse to run the activation script; if so run
`Set-ExecutionPolicy -Scope Process RemoteSigned` once in that window and activate again
(`.venv\Scripts\Activate.ps1`).

## Run

```bat
streamlit run app.py
```

(the browser opens at `http://localhost:8501`; run all commands from the project root).

## Test

```bat
pytest -v
```

Tests that need PLY skip themselves with a clear reason if PLY is missing; the rest are PLY-independent.

## Using the compiler without the UI

```python
from compiler.compiler_pipeline import CompilerPipeline
from compiler.code_fixer import CodeFixer

pipeline = CompilerPipeline()
result = pipeline.analyze(open("samples/syntax_errors.c").read())
print(result.summary())
for d in result.diagnostics:
    print(d.error_id, d.line, d.column, d.message, "->", d.suggestion)

fixer = CodeFixer(result.source)
best = next(c for d in result.diagnostics for c in d.corrections if c.edits)
fixer.apply(best, confirmed=True)       # explicit approval; fixer.undo() reverts
print(pipeline.analyze(fixer.current).summary())
```

## Project layout

```
smart_compiler/
├── app.py                      Streamlit entry point (wiring only)
├── compiler/                   independent of Streamlit
│   ├── tokens.py lexer.py      token model, PLY lexer
│   ├── grammar.py parser.py    PLY grammar rules, parser driver (repair + recovery)
│   ├── ast_nodes.py            AST node classes
│   ├── symbol_table.py type_checker.py semantic_analyzer.py
│   ├── error_manager.py error_classifier.py   diagnostics, catalogue, syntax-error classification
│   ├── correction_rules.py correction_engine.py code_fixer.py
│   └── compiler_pipeline.py    phase controller
├── ui/                         Streamlit panels + session.py (framework-free state logic)
├── utils/                      source locations, diff, validators
├── tests/  samples/  docs/
```

## Verification status

Legend: **Tested** = automated tests executed successfully; **Emulated** = executed only against a
PLY/Streamlit *stand-in written for the sandbox* (not the real library); **Unverified** = never executed.

| Component | Implemented | Status |
|---|---|---|
| AST, symbol table, type checker, semantic analyzer | yes | **Tested** (hand-built ASTs; plain Python) |
| Error manager / catalogue / classifier | yes | **Tested** |
| Edit distance, correction rules and engine, code fixer, UI session controller | yes | **Tested** |
| Lexer (`lexer.py`) | yes | **Emulated** only — real PLY never run |
| Grammar + parser (`grammar.py`, `parser.py`) | yes | **Emulated** only (independent LALR generator found 0 shift/reduce and 0 reduce/reduce conflicts; real PLY table generation never run) |
| Pipeline integration, verified corrections, iterative fixing | yes | **Emulated** only |
| Streamlit UI (`app.py`, `ui/*`) | yes | **Unverified** against real Streamlit (render paths smoke-tested with a recording stub) |
| `streamlit-ace` editor integration | yes | **Unverified** |

Numbers from this build: without PLY, **105 passed / 0 failed / 76 skipped** (the skipped tests are the lexer,
parser, pipeline and end-to-end tests, which need PLY). Against the sandbox PLY emulator and a minimal pytest
stand-in, **226 passed / 0 failed**. These runs do **not** prove the code works with real PLY 3.11 / real pytest —
please run `pytest -v` on your machine; see [docs/testing.md](docs/testing.md) for what to do if something fails.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: ply` | `pip install -r requirements.txt` inside the activated virtual environment |
| `ModuleNotFoundError: compiler` / `utils` / `ui` | run `pytest` / `streamlit run app.py` **from the project root** (`pytest.ini` sets `pythonpath = .`) |
| PLY prints `shift/reduce conflict` or `Generating LALR tables` warnings | report them (the grammar is designed to have none); they appear on stderr only |
| Editor shows a plain text box | `pip install streamlit-ace` (optional component) |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope Process RemoteSigned` |
| Port 8501 busy | `streamlit run app.py --server.port 8502` |
| Stale parser tables | delete `parsetab.py`, `parser.out`, `__pycache__` (tables are not written by default) |

## Documentation

[docs/architecture.md](docs/architecture.md) · [docs/grammar.md](docs/grammar.md) ·
[docs/algorithms.md](docs/algorithms.md) · [docs/user_guide.md](docs/user_guide.md) ·
[docs/testing.md](docs/testing.md) · [docs/project_report.md](docs/project_report.md)

## License

MIT — see [LICENSE](LICENSE).
