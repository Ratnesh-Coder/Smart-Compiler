# Algorithms

## 1. Lexical analysis (`compiler/lexer.py`)
PLY object-style lexer. Function rules run in definition order, then string rules sorted by decreasing
regex length (so `<=` beats `<`, `++` beats `+`). Positions are computed from the token offset with a
`LineIndex` (binary search over line starts), not from PLY's `lineno`, so multi-line comments cannot skew them.

* A *numeric candidate* regex grabs digits, dots and letters greedily; `classify_number` then decides whether
  it is a valid int (decimal, hex, `u/l` suffixes) or float (`.`/exponent, `f` suffix). Invalid candidates
  (`12abc`, `1.2.3`, `1e`, `0x`) raise `LEX002` and are still emitted (flagged `malformed`).
* An unterminated string runs to the end of the line (`LEX003`); an unterminated `/*` consumes to EOF (`LEX004`).
* Bad character literals (`''`, `'ab'`, `'a`) are `LEX005`; `#...` lines are skipped with a `LEX006` note.
* Invalid characters are reported (`LEX001`) and skipped. Nothing is dropped silently.

## 2. Syntax analysis and error handling (`compiler/parser.py`)
PLY builds LALR(1) tables from `CGrammar`. The pre-computed token list is fed through `TokenStream`, which
supports pushing tokens back, so the parser can be repaired mid-parse.

**Precise "expected token" computation.** LALR lookahead sets are merged across contexts, so the raw
`parser.action[state]` keys over-approximate what is acceptable. `_simulate()` therefore replays PLY's own
`action`/`goto`/`productions`/`defaulted_states` tables on a *copy* of the state stack and answers "would the
parser accept these terminals next?". `_expected_terminals()` = every candidate that simulates successfully.

**Repair by token insertion.** On a parse failure at token *T* the parser tries to insert, in order,
`)`, `]`, `;`, and (at EOF only) `}` — accepting an insertion only if `_simulate([inserted, T])` succeeds, so
the repaired program is guaranteed to let the parser continue. `PLY.errok()` plus returning `None` makes PLY
fetch the next lookahead from the stream, which now delivers the synthetic token first. Special cases:
after a function header only `{`/`;` are acceptable → `{` is chosen when the file has more `}` than `{`;
`ID ID` on one line is *not* repaired (it is more likely a misspelt keyword); `;` is never inserted after
`;`, `{` or `}`; `}` is inserted only at EOF, once per open brace (one diagnostic with a count).

**Rollback.** A repair becomes a diagnostic only after the parser makes progress. If the same token fails
again, the pending repair is discarded and a generic `SYN004 / SYN006` is reported. At most one insertion is
allowed per ordinary token; at `}`/EOF successive insertions must follow increasing rank (`)`,`]` → `;` → `}`).

**Panic mode.** Otherwise PLY's `error SEMI` productions resynchronise; PLY suppresses further `p_error`
calls until three tokens were shifted, which limits cascades. The AST is then marked *unreliable*.

**Classification** (`error_classifier.classify_syntax_error`): inserted token → `SYN001/2/3/8`; operator or
`(`/`,`/`=` followed by a terminator → `SYN006` missing operand; EOF → `SYN005`; otherwise `SYN004`.

## 3. Semantic analysis (`semantic_analyzer.py`, `symbol_table.py`, `type_checker.py`)
Single in-order AST walk (C requires declare-before-use). `SymbolTable` is a stack of `Scope`s; closed scopes
are remembered so "declared in a block that has ended" is reported as `SEM011` scope violation rather than a
plain undeclared name. Function bodies share the parameter scope; `for` has its own scope.

Types are strings (`int`, `double`, `int[]`, `int*` for `&x`). `ERROR_TYPE` is compatible with everything so
one mistake yields one diagnostic. Rules: numeric ↔ numeric is legal (narrowing float→int / double→float is a
*warning*, except a floating literal initialising a `float`); string ↔ numeric is an error; `%` needs integers;
arrays only assign to arrays of the same element type; `void` has no value; `scanf` arguments need `&`
(`SEM006`, flagged `missing_address_of`); `printf`/`scanf` format specifiers are counted and type-checked
(`SEM017` warnings). Non-`main` non-void functions that can fall off the end get `SEM013`.

## 4. Correction engine (`correction_engine.py`, `correction_rules.py`)
For each diagnostic: **generate → validate → rank → explain**.

1. **Generate** — rules keyed by diagnostic *kind* plus generic rules:
   * *rule-based*: insert missing `; ) ] }`/`{`, remove/replace invalid characters (smart quotes), valid numeric
     prefix, closing quote placement, `=`→`==` in conditions, reversed operators (`=<`→`<=`, `<>`→`!=`), `&` for `scanf`;
   * *edit distance*: misspelt keywords (identifier not declared anywhere and within distance of a keyword),
     similar **visible** variables/functions (never names outside the current scope);
   * *grammar-based*: insert any punctuation the parser can accept, delete the offending token, placeholder operand
     (only when verification is available);
   * *AST-based*: return-statement fixes, missing return, loop-only statements, array bounds;
   * *symbol-table-based*: declare an undeclared name (type inferred from use), change a variable's declared type,
     prototype for an undeclared function, argument count repair, duplicate-declaration repairs.
2. **Validate** — drop no-op / out-of-range edits and duplicates. With a verifier (default) every candidate is
   applied to a copy and the program is **re-analysed**; it survives only if the diagnostic's signature
   disappears and the errors of its phase *and earlier phases* strictly decrease (later-phase errors may be
   legitimately unmasked, e.g. semantic errors appear once a syntax error is fixed).
3. **Rank** — `score = (wg·grammar + wc·context + ws·symbol + wsim·similarity) / Σw`, default weights
   0.35 / 0.25 / 0.20 / 0.20 (`ScoringWeights`, configurable). `grammar` is 1.0 for verified candidates;
   `similarity` is `1 − damerau(old,new)/max(len)`; `context` and `symbol` come from the rule (e.g. a keyword typo
   scores 1.0 when the next token is an identifier and the keyword is a type).
4. **Explain** — every candidate carries an explanation, method, score breakdown and optional *assumption*.

The resulting `confidence` is a weighted heuristic score clipped to [0.05, 0.99]. **It is not a calibrated
probability** and has not been validated experimentally.

**Edit distance.** `levenshtein` (insert/delete/substitute) and `damerau_levenshtein` (optimal string alignment:
adds adjacent transposition, so `itn→int`, `whlie→while`, `retrun→return` cost 1). Typo threshold: 1 edit for
words up to 4 characters, otherwise 2; a case-only difference counts as 1.

## 5. Safe code fixer (`code_fixer.py`)
`apply_edits` sorts edits by position, rejects overlaps (`OverlappingEditError`) and out-of-range edits, and
rebuilds the text in one left-to-right pass (equal-offset insertions keep their order). `CodeFixer` refuses
stale corrections (SHA-1 of the analysed text), requires `confirmed=True` for corrections with an assumption,
keeps an undo stack, preserves the original, and `apply_many` skips conflicting or unconfirmed corrections
(one undo step for the batch). Indentation is preserved because edits are minimal and inserted lines copy the
indentation of the surrounding code.

## 6. Known limitations of the algorithms
* Multiple simultaneous problems on one statement may need several apply/re-analyse rounds.
* A missing `}` before the next function definition is not recognised as such (it surfaces as other errors).
* The keyword-typo rule needs the misspelt word to be undeclared; a typo that equals a declared name is invisible.
* Semantic analysis on a repaired tree is flagged as *partial* in the result.
* Fix suggestions are heuristics: always review them, especially those that state an assumption.
