# Architecture

All diagrams below reflect the code as implemented (module names are real files).

## 1. System architecture

```mermaid
flowchart LR
    subgraph UI["Streamlit UI (ui/, app.py)"]
        A[app.py] --> D[dashboard] & E[code_editor] & P[error_panel] & C[correction_panel] & R[compilation_report]
        S[session.py<br/>AppController]
    end
    UI --> S
    S --> PL[compiler_pipeline.CompilerPipeline]
    S --> CF[code_fixer.CodeFixer]
    subgraph CORE["compiler/ (no Streamlit dependency)"]
        PL --> LX[lexer.py<br/>PLY Lex] --> TK[tokens.py]
        PL --> PS[parser.py + grammar.py<br/>PLY Yacc] --> AST[ast_nodes.py]
        PL --> SA[semantic_analyzer.py] --> ST[symbol_table.py] & TC[type_checker.py]
        PL --> CE[correction_engine.py] --> CR[correction_rules.py]
        LX & PS & SA & CE --> EM[error_manager.py<br/>error_classifier.py]
    end
```

## 2. Compiler pipeline (what `CompilerPipeline._run` does)

```mermaid
flowchart TD
    SRC[Source text] --> V{validate / normalise}
    V -->|invalid| SK[all phases skipped, reported]
    V --> LEX[Lexical analysis]
    LEX --> TOK[Token stream + lexical diagnostics]
    TOK --> PARSE[Syntax analysis<br/>repair / rollback / panic mode]
    PARSE --> SUP[Suppress cascades caused by lexical errors]
    SUP --> RELIABLE{AST reliable?<br/>no panic recovery,<br/>no grammar-action errors}
    RELIABLE -->|yes| SEM[Semantic analysis<br/>symbol table + type checking]
    RELIABLE -->|no| SKIP[Semantic checks skipped<br/>and listed in the result]
    SEM --> DIAG[Sorted diagnostics]
    SKIP --> DIAG
    DIAG --> CORR[Correction engine<br/>generate - verify - rank - explain]
    CORR --> RES[AnalysisResult]
    RES --> APPROVE[User approval in UI] --> FIX[CodeFixer applies position-based edits] --> SRC
```

## 3. Data flow

```mermaid
flowchart LR
    S[source: str] --> L[CLexer.tokenize] -->|list of Token| P[CParser.parse]
    L -->|Diagnostic| EM[(ErrorManager)]
    P -->|ProgramNode + ParseResult| SA[SemanticAnalyzer.analyze]
    P -->|Diagnostic| EM
    SA -->|SymbolTable| CTX[CorrectionContext<br/>source, tokens, AST, symbols]
    SA -->|Diagnostic + context dict| EM
    EM -->|sorted diagnostics| ENG[CorrectionEngine.attach]
    CTX --> ENG
    ENG -->|Correction = TextEdit list| EM
    ENG -.verify(candidate source).-> PIPE[CompilerPipeline.verify]
    EM --> RES[AnalysisResult] --> UI
```

## 4. Module dependency diagram (generated from the real imports)

```mermaid
flowchart TD
    app --> compiler_pipeline & ui_session[ui.session] & ui_panels[ui.* panels]
    ui_panels --> ui_session
    ui_session --> code_fixer & error_manager
    compiler_pipeline --> lexer & parser & semantic_analyzer & correction_engine & correction_rules & validators[utils.validators]
    lexer --> tokens & error_manager & source_location[utils.source_location]
    parser --> grammar & error_classifier & tokens & error_manager
    grammar --> ast_nodes & tokens & error_manager
    semantic_analyzer --> ast_nodes & symbol_table & type_checker & error_manager
    correction_engine --> correction_rules & code_fixer & error_manager
    correction_rules --> ast_nodes & symbol_table & tokens & error_manager & source_location
    code_fixer --> error_manager
    error_classifier --> error_manager & tokens
    error_manager -.lazy import inside report().-> error_classifier
    error_manager --> source_location
```

`ast_nodes`, `tokens`, `symbol_table`, `type_checker`, `source_location`, `validators` and `code_diff`
depend on nothing else in the project.

## 5. Design decisions

* **Frontend/back-end separation.** `compiler/` never imports Streamlit. All UI state transitions (analyse,
  apply, reject, undo, reset, batch apply) live in `ui/session.py` on top of a `MutableMapping`
  (`st.session_state` in the app, a `dict` in tests), so Streamlit reruns cannot lose code or decisions.
* **PLY is optional for import, mandatory for use.** `compiler/__init__.py` exports lazily, so the
  semantic analyzer, correction engine and code fixer can be imported and tested without PLY.
* **Diagnostics are data.** Every phase reports through `ErrorManager.report(kind, ...)`; the catalogue in
  `error_classifier.py` maps a *kind* to its ID/category/severity. Each diagnostic carries a machine-readable
  `context` dict (offsets, expected tokens, visible symbols, types) used by the correction rules.
* **Corrections are offset-based `TextEdit`s** against the exact analysed text (hash-checked), never global
  string replacement.
* **Thread safety.** The PLY lexer/parser objects are stateful; `CompilerPipeline` guards `analyze`/`verify`
  with an `RLock` because Streamlit sessions run in threads and share the cached pipeline.
* **Deliberate deviations from the suggested layout:** added `ui/session.py` (framework-free state logic),
  `pytest.ini`, and `tests/helpers.py`. No other structural changes.
