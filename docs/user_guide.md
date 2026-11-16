# User guide

## Installation guide (Windows)
1. Install **Python 3.10+** from python.org (tick *Add python.exe to PATH*). Check: `py -3 --version`.
2. Open a terminal in the project folder (`smart_compiler`).
3. Create and activate a virtual environment, install dependencies:
   ```bat
   py -3 -m venv .venv
   .venv\Scripts\activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```
4. Start the application: `streamlit run app.py` (opens http://localhost:8501).
5. Optional self-check: `pytest -v`.

## Using the application
1. **Choose code.** Pick a sample in the sidebar and press *Load sample*, upload a `.c` file, or type/paste code
   in the editor.
2. **Analyze code.** The *Error analysis* tab shows total errors, lexical / syntax / semantic counts, warnings and the
   analysis status. Each diagnostic expands to show ID, category, severity, line, column, message, explanation,
   the offending line with a caret, the suggested correction, a preview and the heuristic confidence.
   Editor gutter markers (when the Ace editor is installed) show the same diagnostics.
3. **Review corrections.** In *Correction workspace* every diagnostic lists a ranked suggestion plus alternatives,
   each with an explanation, method and score breakdown.
   * **Apply correction** modifies the code, then the program is re-analysed automatically.
   * Suggestions marked **Assumption** may change behaviour: read the assumption and tick the confirmation box first.
   * **Reject** hides a suggestion. **Apply all safe fixes** applies the best assumption-free fix of every error at once.
   * **Undo last fix** and **Reset to original** revert changes; the original text is always kept.
   * The *Original versus corrected* section shows a side-by-side view, a unified diff and the corrected file
     (downloadable).
4. **Report.** *Final compilation report* shows errors detected in the first analysis, warnings, corrections applied,
   remaining errors, phase status and whether the program passed all implemented checks (JSON download).
5. **Compiler internals.** Token stream, abstract syntax tree and symbol table of the last analysis.

If you edit the code after analysing, press **Analyze code** again; suggestions are tied to the analysed text and
stale ones are refused rather than applied to the wrong place.

### Reading the status
* **Passed** – no errors or warnings. **Passed with warnings** – no errors. **Failed** – errors.
* **Incomplete** – some checks were skipped (listed explicitly), e.g. semantic analysis is skipped when the syntax
  tree is unusable. Fix the syntax errors and analyse again.

## Error catalogue
| ID | Category | Severity | Title |
|---|---|---|---|
| `LEX001` | Lexical | error | Invalid character |
| `LEX002` | Lexical | error | Malformed numeric literal |
| `LEX003` | Lexical | error | Unterminated string literal |
| `LEX004` | Lexical | error | Unterminated block comment |
| `LEX005` | Lexical | error | Invalid character literal |
| `LEX006` | Lexical | suggestion | Preprocessor directive ignored |
| `SEM001` | Semantic | error | Undeclared identifier |
| `SEM002` | Semantic | error | Duplicate declaration |
| `SEM003` | Semantic | error | Type mismatch |
| `SEM004` | Semantic | error | Incompatible operands |
| `SEM005` | Semantic | error | Wrong number of arguments |
| `SEM006` | Semantic | error | Wrong argument type |
| `SEM007` | Semantic | error | Invalid return |
| `SEM008` | Semantic | error | Invalid array access |
| `SEM009` | Semantic | error | Void value used in an expression |
| `SEM010` | Semantic | error | Undeclared function |
| `SEM011` | Semantic | error | Identifier out of scope |
| `SEM012` | Semantic | error | 'break'/'continue' outside a loop |
| `SEM013` | Semantic | warning | Missing return statement |
| `SEM014` | Semantic | warning | Implicit narrowing conversion |
| `SEM015` | Semantic | error | Conflicting function declaration |
| `SEM016` | Semantic | warning | Division by zero |
| `SEM017` | Semantic | warning | Format string mismatch |
| `SEM018` | Semantic | error | Invalid declaration |
| `SEM019` | Semantic | error | Not a function |
| `SEM020` | Semantic | error | Invalid assignment |
| `SEM022` | Semantic | warning | Assignment used as a condition |
| `SYN001` | Syntax | error | Missing semicolon |
| `SYN002` | Syntax | error | Missing closing parenthesis |
| `SYN003` | Syntax | error | Missing brace |
| `SYN004` | Syntax | error | Unexpected token |
| `SYN005` | Syntax | error | Unexpected end of file |
| `SYN006` | Syntax | error | Missing operand |
| `SYN008` | Syntax | error | Missing closing bracket |
| `SYN009` | Syntax | error | Invalid assignment target |
| `SYN010` | Syntax | error | Invalid function call |

(`SYN007` and `SEM021` are intentionally unused.)

## Worked examples
* `int @number = 10;` → `LEX001` at line 1, column 5, suggestion: remove the invalid character.
* `int x = 10` followed by another statement → `SYN001`, insert `;` right after `10`.
* `itn x = 5;` → `SYN004`; top correction: replace `itn` with `int` (edit distance 1, transposition).
* `int age = "hello";` → `SEM003`: *Cannot assign a string literal to an integer variable.* with an alternative that
  declares `age` as a `char` array (requires confirming the assumption).
* `total = 100;` without a declaration → `SEM001`; the correction is to declare `int total;` at the start of the function
  (assumption: type inferred from use); if a similarly named variable is visible (e.g. `totl`), replacing the name
  is offered and ranked first.
