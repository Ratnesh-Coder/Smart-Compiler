# Smart Compiler Error Detection and Correction System — Project Report

## 1. Abstract
Conventional compilers report errors but rarely help programmers fix them. This project implements a compiler front
end for a subset of C (PLY lexer and LALR parser, AST, symbol table, type checker) combined with a hybrid
correction engine that generates, verifies, ranks and explains context-aware fixes. A safe, position-based code
fixer and a Streamlit interface let users approve, reject, undo and re-analyse corrections.

## 2. Introduction
Students typically meet compiler diagnostics such as "expected ';' before 'int'" without an explanation of the cause or
a concrete fix. The project reuses classical compiler-construction techniques (lexical, syntax and semantic analysis)
and adds an intelligent layer that turns each diagnostic into reviewable, explained corrections.

## 3. Problem statement
Given C source code, detect lexical, syntax and semantic errors, explain their causes and suggest meaningful corrections
without silently modifying the program, while keeping cascading, misleading diagnostics to a minimum.

## 4. Objectives
1. Real compiler phases built on PLY (lexer, parser) with an AST, symbol table and type checker.
2. A central, structured diagnostic system with stable error IDs.
3. A hybrid correction engine (rules, edit distance, grammar, AST, symbol table) with ranking and explanations.
4. Safe, explicit, reversible code modification and re-analysis.
5. A usable web UI, an automated test suite and honest documentation of limits.

## 5. Existing system
GCC/Clang diagnostics (some "did you mean" hints, fix-it hints), IDE quick-fixes, linters (cppcheck, clang-tidy).
Academic work includes error-repair parsers (e.g. Burke–Fisher, panic mode) and neural program-repair models.

## 6. Limitations of existing systems
Terse messages aimed at experts; cascading errors after one mistake; few alternatives and no explanation of
ranking; fixes are not verified against the full grammar and type rules; learners rarely see *why* a fix is suggested.

## 7. Proposed system
A modular pipeline `source → lexer → parser → AST → semantic analysis → diagnostics → correction engine → user approval
→ code fixer → re-analysis → report`. Corrections are *verified* by re-analysing the corrected text, ranked with a
configurable weighted score and always applied only with explicit consent.

## 8. System architecture
See [architecture.md](architecture.md) for the system, pipeline, data-flow and module-dependency diagrams (all generated
from the implemented modules).

## 9. Compiler design concepts used
Regular-expression based scanning, LALR(1) parsing with precedence, syntax-directed AST construction, symbol tables with
nested scopes, type systems with implicit conversions, error recovery (panic mode, insertion repair), diagnostics design.

## 10. Lexical analysis
`compiler/lexer.py` (PLY Lex). Keywords, identifiers, int/float/hex constants, char/string literals, operators,
delimiters, comments. Accurate line/column; errors LEX001–LEX006. See [algorithms.md](algorithms.md#1-lexical-analysis-compilerlexerpy).

## 11. Syntax analysis
`compiler/grammar.py` + `compiler/parser.py` (PLY Yacc). Grammar in [grammar.md](grammar.md). Error handling: token
insertion verified by table simulation, rollback, PLY panic mode; SYN001–SYN010.

## 12. Abstract syntax tree
`compiler/ast_nodes.py`: `ProgramNode, DeclarationNode, DeclaratorNode, FunctionNode, ParameterNode, CompoundNode,
IfNode, WhileNode, DoWhileNode, ForNode, ReturnNode, BreakNode, ContinueNode, AssignmentNode, BinaryOperationNode,
UnaryOperationNode, FunctionCallNode, ArrayAccessNode, IdentifierNode, LiteralNode, InitializerListNode,
ExpressionStatementNode, ErrorStatementNode`. Every node keeps line, column, offset and length.

## 13. Semantic analysis
`compiler/semantic_analyzer.py` walks the AST once: declarations, scopes, expression typing, assignments, calls
(argument count/type, `printf`/`scanf` formats), returns, array indexing, loop-only statements; SEM001–SEM022.

## 14. Symbol table
`compiler/symbol_table.py`: `Symbol` (name, type, category, scope name/level, declaration location and offsets, parameters,
array size, flags) and a stack of `Scope`s; closed scopes are retained for scope-violation explanations.

## 15. Error classification
`compiler/error_manager.py` (`Diagnostic`, `Correction`, `TextEdit`, `ErrorManager`) and `compiler/error_classifier.py`
(catalogue: kind → ID, category, severity; parser-failure classification). Full catalogue: [user_guide.md](user_guide.md#error-catalogue).

## 16. Error detection algorithms
Lexer regex classification; parser state simulation and repair; semantic checks with `ERROR_TYPE` to avoid cascades;
cascade suppression between phases (see pipeline docstring). Details in [algorithms.md](algorithms.md).

## 17. Intelligent correction algorithms
Candidate generation per diagnostic kind, validation by re-analysis (phase-aware strict error reduction), weighted ranking
(grammar 0.35, context 0.25, symbol 0.20, similarity 0.20, configurable), explanation and assumptions. Edit distance:
Levenshtein and Damerau-Levenshtein (OSA).

## 18. System modules
| Module | Responsibility |
|---|---|
| `tokens.py`, `lexer.py` | token model, PLY lexer |
| `grammar.py`, `parser.py`, `ast_nodes.py` | grammar, parser driver, AST |
| `symbol_table.py`, `type_checker.py`, `semantic_analyzer.py` | semantic phase |
| `error_manager.py`, `error_classifier.py` | diagnostics |
| `correction_rules.py`, `correction_engine.py`, `code_fixer.py` | suggestions and safe edits |
| `compiler_pipeline.py` | phase orchestration, recovery policy |
| `ui/*`, `app.py` | Streamlit interface; `ui/session.py` holds all state logic |
| `utils/*` | source locations, diff, validation |

## 19. Implementation details
Python 3.10+, dataclasses, type hints, `pathlib` for paths, no absolute paths, PLY 3.11 (object-style `lex`/`yacc`),
Streamlit with optional `streamlit-ace`. About 6,500 lines including tests. The pipeline is guarded by an `RLock`.
The parser relies on PLY 3.11 internals (`statestack`, `action`, `goto`, `productions`, `defaulted_states`).

## 20. Testing and results
See [testing.md](testing.md). Summary (honest): PLY-independent tests **105 passed / 0 failed**; the lexer, parser, pipeline
and end-to-end tests (**76 test functions**) are skipped without PLY. Against a sandbox PLY *emulator* the whole suite
(**226 cases**) passed, which is supporting evidence only. **Real PLY, real pytest and real Streamlit were not run.**

## 21. Advantages
One precise diagnostic per real mistake in common cases; explained, verified and ranked alternatives; no automatic
changes; reversible edits; semantic analysis continues on repaired trees (flagged partial); modular and testable
without PLY or Streamlit for most components.

## 22. Limitations
Subset of C only (see README); heuristic confidence not calibrated; parser repair covers `; ) ] }` and `{` insertion only;
some multi-error situations need several rounds; keyword-typo detection needs the typo to be undeclared; correctness against
real PLY/Streamlit unverified in this build; no cross-validation against GCC was performed.

## 23. Future scope
Pointers, structs, `switch`, preprocessor/macros, casts; richer repair (token substitution/deletion with simulation);
learned ranking with experimentally calibrated confidence; GCC cross-validation harness; LSP/VS Code integration;
persisted analysis history.

## 24. Conclusion
The project demonstrates a complete compiler front end with an explainable, verified correction layer and a safe editing
workflow. The remaining step before academic submission is to run the test suite and the application against the real
dependencies and fix anything this environment could not reveal.

## 25. References
1. Aho, Lam, Sethi, Ullman — *Compilers: Principles, Techniques, and Tools* (2nd ed.), Pearson, 2006.
2. Appel — *Modern Compiler Implementation in C*, Cambridge University Press, 1998.
3. Beazley — *PLY (Python Lex-Yacc)* documentation, https://www.dabeaz.com/ply/
4. Levenshtein — "Binary codes capable of correcting deletions, insertions and reversals", *Soviet Physics Doklady* 10(8), 1966.
5. Damerau — "A technique for computer detection and correction of spelling errors", *CACM* 7(3), 1964.
6. Burke, Fisher — "A practical method for LR and LL syntactic error diagnosis and recovery", *ACM TOPLAS* 9(2), 1987.
7. Kernighan, Ritchie — *The C Programming Language* (2nd ed.), Prentice Hall, 1988.
8. Streamlit documentation, https://docs.streamlit.io/
