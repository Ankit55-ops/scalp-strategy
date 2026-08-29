"""Recursive-descent parser building an AST from DSL tokens."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.dsl.tokenizer import TOKEN_TYPES, Token, TokenizeError, tokenize

# Allow-listed functions and their arity. Anything else is a parse/eval error.
ALLOWED_FUNCTIONS: dict[str, int | tuple[int, int]] = {
    "ema": 2,
    "sma": 2,
    "rsi": 2,
    "atr": 2,
    "crossover": 2,
    "crossunder": 2,
    "highest": 2,
    "lowest": 2,
    "stdev": 2,
    "abs": 1,
    "min": (2, 3),
    "max": (2, 3),
}

ALLOWED_BINARY_OPS = {"+", "-", "*", "/", "%", "==", "!=", ">=", "<=", ">", "<"}
ALLOWED_LOGICAL_OPS = {"and", "or"}


class ParseError(Exception):
    pass


@dataclass
class Node:
    kind: str
    value: Any = None
    left: "Node | None" = None
    right: "Node | None" = None
    args: list["Node"] = field(default_factory=list)
    name: str | None = None


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def expect(self, type_: str) -> Token:
        tok = self.peek()
        if tok.type != type_:
            raise ParseError(f"expected {type_} but got {tok.value!r} at {tok.pos}")
        return self.advance()

    def parse(self) -> Node:
        node = self.parse_or()
        tok = self.peek()
        if tok.type != TOKEN_TYPES["EOF"]:
            raise ParseError(f"unexpected token {tok.value!r} at {tok.pos}")
        return node

    def parse_or(self) -> Node:
        left = self.parse_and()
        while self.peek().type == TOKEN_TYPES["IDENT"] and (
            self.peek().value.lower() == "or"
        ):
            self.advance()
            right = self.parse_and()
            left = Node("logical", "or", left=left, right=right)
        return left

    def parse_and(self) -> Node:
        left = self.parse_not()
        while self.peek().type == TOKEN_TYPES["IDENT"] and (
            self.peek().value.lower() == "and"
        ):
            self.advance()
            right = self.parse_not()
            left = Node("logical", "and", left=left, right=right)
        return left

    def parse_not(self) -> Node:
        if self.peek().type == TOKEN_TYPES["IDENT"] and self.peek().value.lower() == "not":
            self.advance()
            operand = self.parse_not()
            return Node("logical", "not", left=operand)
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        left = self.parse_arith()
        while self.peek().type == TOKEN_TYPES["OP"] and self.peek().value in ALLOWED_BINARY_OPS:
            op = self.advance().value
            right = self.parse_arith()
            left = Node("binary", op, left=left, right=right)
        return left

    def parse_arith(self) -> Node:
        left = self.parse_term()
        while self.peek().type == TOKEN_TYPES["OP"] and self.peek().value in ("+", "-"):
            op = self.advance().value
            right = self.parse_term()
            left = Node("binary", op, left=left, right=right)
        return left

    def parse_term(self) -> Node:
        left = self.parse_unary()
        while self.peek().type == TOKEN_TYPES["OP"] and self.peek().value in ("*", "/", "%"):
            op = self.advance().value
            right = self.parse_unary()
            left = Node("binary", op, left=left, right=right)
        return left

    def parse_unary(self) -> Node:
        if self.peek().type == TOKEN_TYPES["OP"] and self.peek().value == "-":
            self.advance()
            operand = self.parse_unary()
            return Node("unary", "-", left=operand)
        return self.parse_primary()

    def parse_primary(self) -> Node:
        tok = self.peek()
        if tok.type == TOKEN_TYPES["NUMBER"]:
            self.advance()
            return Node("literal", tok.value)
        if tok.type == TOKEN_TYPES["STRING"]:
            self.advance()
            return Node("literal", tok.value)
        if tok.type == TOKEN_TYPES["BOOL"]:
            self.advance()
            return Node("literal", tok.value)
        if tok.type == TOKEN_TYPES["NULL"]:
            self.advance()
            return Node("literal", None)
        if tok.type == TOKEN_TYPES["LPAREN"]:
            self.advance()
            node = self.parse_or()
            self.expect(TOKEN_TYPES["RPAREN"])
            return node
        if tok.type == TOKEN_TYPES["IDENT"]:
            self.advance()
            if self.peek().type == TOKEN_TYPES["LPAREN"]:
                self.advance()
                name = tok.value.lower()
                if name not in ALLOWED_FUNCTIONS:
                    raise ParseError(f"function '{tok.value}' is not allow-listed")
                args: list[Node] = []
                if self.peek().type != TOKEN_TYPES["RPAREN"]:
                    args.append(self.parse_or())
                    while self.peek().type == TOKEN_TYPES["COMMA"]:
                        self.advance()
                        args.append(self.parse_or())
                self.expect(TOKEN_TYPES["RPAREN"])
                allowed = ALLOWED_FUNCTIONS[name]
                if isinstance(allowed, int):
                    if len(args) != allowed:
                        raise ParseError(
                            f"function '{name}' expects {allowed} args, got {len(args)}"
                        )
                else:
                    lo, hi = allowed
                    if not (lo <= len(args) <= hi):
                        raise ParseError(
                            f"function '{name}' expects {lo}-{hi} args, got {len(args)}"
                        )
                return Node("call", name=name, args=args)
            return Node("symbol", tok.value)
        raise ParseError(f"unexpected token {tok.value!r} at {tok.pos}")


def parse_expression(expr: str) -> Node:
    try:
        tokens = tokenize(expr)
    except TokenizeError as exc:
        raise ParseError(f"tokenize error: {exc}") from exc
    return Parser(tokens).parse()
