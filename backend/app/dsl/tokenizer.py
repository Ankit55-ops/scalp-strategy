"""Safe, allow-listed strategy rule expression engine.

No eval() or exec() is ever used. Expressions are tokenized and parsed into
an AST, then evaluated against a bounded environment of symbols and
functions that is explicitly allow-listed.

Supported grammar (operators / functions only from the allow-list):

  expression := or_expr
  comparisons: ==, !=, >, >=, <, <=
  boolean:     and, or, not
  arithmetic:  +, -, *, /, %
  functions:   ema, sma, rsi, atr, macd, macd_signal, crossover,
               crossunder, highest, lowest, stdev, cross
  values:      numbers, strings, booleans, null

Anything else is rejected during parsing or evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

TOKEN_TYPES = {
    "NUMBER": "NUMBER",
    "STRING": "STRING",
    "IDENT": "IDENT",
    "OP": "OP",
    "LPAREN": "LPAREN",
    "RPAREN": "RPAREN",
    "COMMA": "COMMA",
    "BOOL": "BOOL",
    "NULL": "NULL",
    "EOF": "EOF",
}

OPERATORS = ["==", "!=", ">=", "<=", ">", "<", "+", "-", "*", "/", "%", "="]


@dataclass
class Token:
    type: str
    value: Any
    pos: int


class TokenizeError(Exception):
    pass


def _dsl_caps() -> tuple[int, int]:
    from app.core.config import get_settings

    s = get_settings()
    return s.MAX_EXPRESSION_LENGTH, s.MAX_EXPRESSION_TOKENS


def tokenize(expr: str) -> list[Token]:
    max_len, max_tokens = _dsl_caps()
    if len(expr) > max_len:
        raise TokenizeError(
            f"expression exceeds {max_len} character limit"
        )
    tokens: list[Token] = []
    i = 0
    n = len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        # string literal
        if ch in ('"', "'"):
            quote = ch
            j = i + 1
            buf = []
            while j < n and expr[j] != quote:
                buf.append(expr[j])
                j += 1
            if j >= n:
                raise TokenizeError(f"unterminated string at {i}")
            tokens.append(Token(TOKEN_TYPES["STRING"], "".join(buf), i))
            i = j + 1
            continue
        # operators (longest match)
        matched_op = None
        for op in sorted(OPERATORS, key=len, reverse=True):
            if expr.startswith(op, i):
                matched_op = op
                break
        if matched_op is not None:
            tokens.append(Token(TOKEN_TYPES["OP"], matched_op, i))
            i += len(matched_op)
            continue
        if ch == "(":
            tokens.append(Token(TOKEN_TYPES["LPAREN"], "(", i))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token(TOKEN_TYPES["RPAREN"], ")", i))
            i += 1
            continue
        if ch == ",":
            tokens.append(Token(TOKEN_TYPES["COMMA"], ",", i))
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and expr[i + 1].isdigit()):
            j = i
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            try:
                num = float(expr[i:j])
            except ValueError as exc:
                raise TokenizeError(f"bad number at {i}") from exc
            tokens.append(Token(TOKEN_TYPES["NUMBER"], num, i))
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (expr[j].isalnum() or expr[j] == "_"):
                j += 1
            word = expr[i:j]
            if word.lower() == "true":
                tokens.append(Token(TOKEN_TYPES["BOOL"], True, i))
            elif word.lower() == "false":
                tokens.append(Token(TOKEN_TYPES["BOOL"], False, i))
            elif word.lower() == "null":
                tokens.append(Token(TOKEN_TYPES["NULL"], None, i))
            else:
                tokens.append(Token(TOKEN_TYPES["IDENT"], word, i))
            i = j
            continue
        raise TokenizeError(f"unexpected character '{ch}' at {i}")
    if len(tokens) > max_tokens:
        raise TokenizeError(f"expression exceeds {max_tokens} token limit")
    tokens.append(Token(TOKEN_TYPES["EOF"], None, n))
    return tokens
