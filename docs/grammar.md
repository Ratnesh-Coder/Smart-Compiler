# Grammar

Implemented in `compiler/grammar.py` (`CGrammar`, PLY docstring productions). Start symbol: `translation_unit`.

## Productions

```
translation_unit       : external_declaration_list
external_declaration_list : external_declaration_list external_declaration | external_declaration
external_declaration   : function_definition | function_declaration | declaration | error SEMI

declaration            : type_specifier init_declarator_list SEMI
type_specifier         : INT | FLOAT | DOUBLE | CHAR | VOID
init_declarator_list   : init_declarator_list COMMA init_declarator | init_declarator
init_declarator        : ID
                       | ID ASSIGN initializer
                       | ID LBRACKET expression RBRACKET
                       | ID LBRACKET expression RBRACKET ASSIGN initializer
                       | ID LBRACKET RBRACKET ASSIGN initializer
initializer            : expression | LBRACE initializer_list RBRACE
initializer_list       : initializer_list COMMA expression | expression

function_definition    : type_specifier ID LPAREN [parameter_list] RPAREN compound_statement
function_declaration   : type_specifier ID LPAREN [parameter_list] RPAREN SEMI
parameter_list         : parameter_list COMMA parameter | parameter
parameter              : type_specifier | type_specifier ID
                       | type_specifier ID LBRACKET RBRACKET | type_specifier ID LBRACKET expression RBRACKET

compound_statement     : LBRACE RBRACE | LBRACE block_item_list RBRACE
block_item_list        : block_item_list block_item | block_item
block_item             : declaration | statement
statement              : compound_statement | expression_statement | selection_statement
                       | iteration_statement | jump_statement | error SEMI
expression_statement   : SEMI | expression SEMI
selection_statement    : IF LPAREN expression RPAREN statement                    %prec LOWER_THAN_ELSE
                       | IF LPAREN expression RPAREN statement ELSE statement
iteration_statement    : WHILE LPAREN expression RPAREN statement
                       | DO statement WHILE LPAREN expression RPAREN SEMI
                       | FOR LPAREN for_init opt_expression SEMI opt_expression RPAREN statement
for_init               : declaration | expression SEMI | SEMI
opt_expression         : expression | empty
jump_statement         : BREAK SEMI | CONTINUE SEMI | RETURN SEMI | RETURN expression SEMI

expression             : expression (ASSIGN|PLUS_ASSIGN|MINUS_ASSIGN|TIMES_ASSIGN|DIVIDE_ASSIGN|MOD_ASSIGN) expression
                       | expression (LOR|LAND|EQ|NE|LT|LE|GT|GE|PLUS|MINUS|TIMES|DIVIDE|MOD) expression
                       | unary_expression
unary_expression       : postfix_expression | (MINUS|PLUS|LNOT|AMP|INC|DEC) unary_expression
postfix_expression     : primary_expression
                       | postfix_expression LBRACKET expression RBRACKET
                       | postfix_expression LPAREN [argument_list] RPAREN
                       | postfix_expression (INC|DEC)
primary_expression     : ID | INT_CONST | FLOAT_CONST | CHAR_CONST | STRING_LITERAL | LPAREN expression RPAREN
```

(`[x]` marks two separate productions in the code; `a|b` inside parentheses is an abbreviation of one
production per alternative.)

## Precedence and associativity (`CGrammar.precedence`, lowest to highest)

| Level | Operators | Associativity |
|---|---|---|
| 1 | `LOWER_THAN_ELSE` (pseudo), `else` | non-assoc (resolves dangling else: `else` binds to the nearest `if`) |
| 2 | `= += -= *= /= %=` | right |
| 3 | `\|\|` | left |
| 4 | `&&` | left |
| 5 | `== !=` | left |
| 6 | `< <= > >=` | left |
| 7 | `+ -` | left |
| 8 | `* / %` | left |
| – | prefix unary, postfix `[] () ++ --` | dedicated non-terminals, bind tighter than every binary operator |

Assignment is parsed as a binary expression; the grammar *action* rejects a left-hand side that is not an
identifier or array element (`SYN009`).

## Error productions

`statement : error SEMI` and `external_declaration : error SEMI` provide PLY panic-mode synchronisation at
semicolons. Most syntax errors never reach them because of token-insertion repair (see `algorithms.md`).

## Conflict analysis

PLY itself reports conflicts on stderr when it generates the tables. It could not be run while building
this project. The grammar was instead fed to an **independent LALR(1) generator written for the sandbox**
(not part of this repository): it found **0 shift/reduce and 0 reduce/reduce conflicts** (190 states,
105 productions), and it correctly reports the dangling-else conflict if the `LOWER_THAN_ELSE` precedence is
removed. Treat this as strong evidence, not proof, for the real PLY tables.

## Unsupported grammar (future work)

pointers and `*` declarators, `struct`/`union`/`enum`, `switch`/`case`, `goto`, `?:`, bitwise and shift
operators, casts, `sizeof`, comma operator, multi-dimensional arrays, `typedef`, qualifiers (`const`,
`static`, `unsigned`, `long`, ...), function pointers, preprocessor directives (skipped by the lexer).
