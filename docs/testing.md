# Testing

## How to run
```bat
pytest -v                       :: everything
pytest -v tests/test_semantic.py
```
`pytest.ini` sets `pythonpath = .`, so run from the project root. Modules that need PLY call
`pytest.importorskip("ply")` / use the `needs_ply` marker and skip with an explicit reason if PLY is absent.

## Test files and what they cover

| File | Needs PLY | Cases (functions) | Content |
|---|---|---|---|
| `test_ast.py` | no | 7 | node defaults, children/walk, tree rendering, identity equality |
| `test_semantic.py` | no (3 e2e tests yes) | 55 | type rules, symbol table scopes, every semantic error kind, valid conversions, no-cascade, printf/scanf |
| `test_correction.py` | no | 46 | edit distance, lexical/symbol/AST rules, ranking and weights, verifier filtering, code fixer (apply, undo, overlap, stale, confirmation, batch), UI controller state (stub analyzer) |
| `test_lexer.py` | yes | 19 | tokens, operators, literals, comments, positions, all lexical errors, preprocessor, reuse |
| `test_parser.py` | yes | 33 | valid constructs, precedence/associativity, dangling else, locations, every syntax error kind, recovery, 18 malformed inputs |
| `test_pipeline.py` | yes | 21 | phase status, skipped checks, multi-phase errors, suppression, hostile input, verified suggestions, iterative repair to a clean program, controller with the real pipeline |

Parametrised cases expand the counts (the PLY-emulated run executed 226 test cases in total).

## Results actually obtained in this build (honest summary)

| Run | Result |
|---|---|
| No PLY / no pytest (the sandbox's real state), minimal pytest stand-in | **105 passed, 0 failed, 76 skipped** (the 76 need PLY) |
| Sandbox PLY *emulator* + minimal pytest stand-in | **226 passed, 0 failed, 0 skipped** |
| **Real PLY 3.11 + real pytest** | **not executed** — run `pytest -v` yourself |
| Streamlit UI | render functions smoke-tested with a recording stub only; `streamlit run app.py` **not executed** |

The emulator (independent LALR(1) generator + a port of PLY's parse/recovery loop + lex emulation) and the pytest
stand-in were written for this sandbox, live outside the project (`verification_sandbox/` next to it) and are
**not** evidence that real PLY behaves identically. They did find and fix real bugs (e.g. a `None` token queued at
EOF repair, LALR over-approximated expected sets, over-strict candidate verification).

## If something fails on your machine
1. `pip install -r requirements.txt` in the activated virtual environment; `pytest --version`.
2. Run `pytest -x -v` and send the first traceback. Most likely suspects, in order: PLY 3.11 attribute names used by
   `CParser._simulate` (`parser.statestack`, `.action`, `.goto`, `.productions`, `.defaulted_states`); PLY table
   warnings on stderr; a Windows-specific path/encoding issue in tests reading `samples/` (files are read as UTF-8).
3. Tests are independent of each other; failures in `test_lexer.py` / `test_parser.py` do not affect the PLY-free tests.

## Sample programs (`samples/`)
`valid_program.c` (0 errors, 0 warnings), `lexical_errors.c` (LEX001, 2×LEX002, LEX003), `syntax_errors.c`
(SYN001, SYN002, SYN004, SYN002), `semantic_errors.c` (every semantic category incl. warnings). The expected
diagnostics are asserted by tests; they are **computed by the compiler**, never hard-coded into it.

## Not tested / known gaps
Real-library behaviour (above); the Ace editor component; concurrent sessions; very large inputs beyond the
200 000-character limit; the optional use of GCC for cross-validation (not implemented).
