"""Lexer and parser for Pine Script (v4, v5 and v6 syntax used by strategies).

The parser yields a small tuple-based AST:

    expressions  ("num", v) ("str", s) ("bool", b) ("na",) ("name", "ta.sma") ("hist", e, n)
                 ("call", name, args, kwargs) ("bin", op, a, b) ("and", a, b) ("or", a, b)
                 ("not", a) ("neg", a) ("tern", c, a, b) ("tuple", [e])
                 ("ifx", branches, else_block) ("switchx", subject, cases)
    statements   ("decl", name, e, persistent) ("assign", name, e) ("aug", name, op, e)
                 ("tdecl", names, e) ("expr", e) ("if", branches, else_block)
                 ("for", var, start, end, step, block) ("while", cond, block)
                 ("break",) ("continue",) ("fn", name, params, block)

Every statement is wrapped as (line, stmt) so errors can point at the script line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .runtime import PineError

KEYWORDS = {"if", "else", "for", "to", "by", "while", "var", "varip", "true", "false", "and", "or", "not",
            "switch", "break", "continue", "import", "export", "method", "type", "in"}
TYPE_WORDS = {"int", "float", "bool", "string", "color", "line", "label", "box", "table", "linefill",
              "polyline", "series", "simple", "const", "input"}
OPS = [":=", "==", "!=", "<=", ">=", "=>", "+=", "-=", "*=", "/=", "%=",
       "+", "-", "*", "/", "%", "<", ">", "=", "?", ":", ",", "(", ")", "[", "]"]
CONTINUATION_END = {"+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!=", "?", ":", ",", "(", "[",
                    "and", "or", "not"}
UNSUPPORTED_NS = ("array.", "matrix.", "map.")

_TOKEN = re.compile(r"""
    (?P<ws>[ \t]+)
  | (?P<comment>//.*)
  | (?P<num>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<color>\#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
  | (?P<op>:=|==|!=|<=|>=|=>|\+=|-=|\*=|/=|%=|[-+*/%<>=?:,()\[\]])
""", re.VERBOSE)


@dataclass
class Tok:
    kind: str     # num str color name kw op
    value: object
    line: int


@dataclass
class Line:
    indent: int
    toks: list
    line: int


def _unescape(s: str) -> str:
    body = s[1:-1]
    return body.encode("utf-8").decode("unicode_escape") if "\\" in body else body


def tokenize(source: str) -> tuple[list[Line], int]:
    """Split the script into logical lines (continuations joined). Returns (lines, version)."""
    version = 0
    m = re.search(r"//\s*@version\s*=\s*(\d+)", source)
    if m:
        version = int(m.group(1))
    lines: list[Line] = []
    depth = 0
    for lineno, raw in enumerate(source.splitlines(), start=1):
        text = raw.rstrip()
        stripped = text.lstrip(" \t")
        if not stripped or stripped.startswith("//"):
            continue
        indent = 0
        for ch in text[: len(text) - len(stripped)]:
            indent += 4 if ch == "\t" else 1
        toks: list[Tok] = []
        pos = 0
        while pos < len(stripped):
            m = _TOKEN.match(stripped, pos)
            if not m:
                raise PineError(f"Unexpected character {stripped[pos]!r}", lineno)
            pos = m.end()
            kind = m.lastgroup
            val = m.group()
            if kind in ("ws", "comment"):
                if kind == "comment":
                    break
                continue
            if kind == "num":
                v = float(val) if any(c in val for c in ".eE") else int(val)
                toks.append(Tok("num", v, lineno))
            elif kind == "str":
                toks.append(Tok("str", _unescape(val), lineno))
            elif kind == "color":
                toks.append(Tok("color", val, lineno))
            elif kind == "name":
                if val in KEYWORDS:
                    toks.append(Tok("kw", val, lineno))
                else:
                    if val.startswith(UNSUPPORTED_NS):
                        raise PineError(f"'{val}': Pine arrays, matrices and maps are not supported yet", lineno)
                    toks.append(Tok("name", val, lineno))
            else:
                toks.append(Tok("op", val, lineno))
        if not toks:
            continue
        cont = False
        if lines:
            last = lines[-1].toks[-1]
            if depth > 0:
                cont = True
            elif indent % 4 != 0 and indent > lines[-1].indent:
                cont = True
            elif last.kind in ("op", "kw") and last.value in CONTINUATION_END:
                cont = True
        if cont:
            lines[-1].toks.extend(toks)
        else:
            lines.append(Line(indent, toks, lineno))
        for t in toks:
            if t.kind == "op":
                if t.value in ("(", "["):
                    depth += 1
                elif t.value in (")", "]"):
                    depth = max(0, depth - 1)
    return lines, version


class Stream:
    def __init__(self, toks: list[Tok], line: int):
        self.toks, self.i, self.line = toks, 0, line

    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def next(self) -> Tok:
        t = self.peek()
        if t is None:
            raise PineError("Unexpected end of line", self.line)
        self.i += 1
        return t

    def at(self, kind: str, value=None, k: int = 0) -> bool:
        t = self.peek(k)
        return t is not None and t.kind == kind and (value is None or t.value == value)

    def accept(self, kind: str, value=None) -> bool:
        if self.at(kind, value):
            self.i += 1
            return True
        return False

    def expect(self, kind: str, value=None) -> Tok:
        t = self.peek()
        if t is None or t.kind != kind or (value is not None and t.value != value):
            got = "end of line" if t is None else repr(t.value)
            raise PineError(f"Expected {value or kind} but found {got}", t.line if t else self.line)
        self.i += 1
        return t

    def done(self) -> bool:
        return self.i >= len(self.toks)

    def end(self) -> None:
        if not self.done():
            t = self.peek()
            raise PineError(f"Unexpected {t.value!r}", t.line)


class Parser:
    def __init__(self, lines: list[Line]):
        self.lines, self.pos = lines, 0

    # ------------------------------------------------------------ blocks

    def program(self) -> list:
        if not self.lines:
            raise PineError("The script is empty")
        base = self.lines[0].indent
        block = self.block(base)
        if self.pos < len(self.lines):
            ln = self.lines[self.pos]
            raise PineError("Unexpected indentation", ln.line)
        return block

    def block(self, indent: int) -> list:
        out = []
        while self.pos < len(self.lines):
            ln = self.lines[self.pos]
            if ln.indent < indent:
                break
            if ln.indent > indent:
                raise PineError("Unexpected indentation", ln.line)
            out.append(self.statement())
        return out

    def child(self, parent_indent: int, line: int) -> list:
        if self.pos >= len(self.lines) or self.lines[self.pos].indent <= parent_indent:
            raise PineError("Expected an indented block", line)
        return self.block(self.lines[self.pos].indent)

    # -------------------------------------------------------- statements

    def statement(self):
        ln = self.lines[self.pos]
        self.pos += 1
        s = Stream(ln.toks, ln.line)
        return ln.line, self._statement(ln, s)

    def _statement(self, ln: Line, s: Stream):
        t = s.peek()
        if t.kind == "kw":
            v = t.value
            if v == "if":
                s.next()
                return self.if_(ln, s, expr=False)
            if v == "for":
                return self.for_(ln, s)
            if v == "while":
                s.next()
                cond = self.expr(s)
                s.end()
                return ("while", cond, self.child(ln.indent, ln.line))
            if v == "switch":
                s.next()
                return ("expr", self.switch_(ln, s))
            if v in ("break", "continue"):
                s.next()
                s.end()
                return (v,)
            if v in ("var", "varip"):
                s.next()
                name = self._decl_name(s)
                s.expect("op", "=")
                return ("decl", name, self.rhs(ln, s), True)
            if v == "import":
                raise PineError("Pine libraries (import) are not supported; paste the functions you need into "
                                "the script instead", ln.line)
            if v in ("type", "method", "export"):
                raise PineError(f"'{v}' declarations are not supported yet", ln.line)
        # function definition: name(params) =>
        if t.kind == "name" and s.at("op", "(", 1):
            j = self._match(s, 1)
            if j is not None and j + 1 < len(s.toks) and s.toks[j + 1].kind == "op" and s.toks[j + 1].value == "=>":
                return self.function(ln, s, j)
        # tuple declaration: [a, b] = expr
        if s.at("op", "["):
            j = self._match(s, 0)
            if j is not None and j + 1 < len(s.toks) and s.toks[j + 1].kind == "op" \
                    and s.toks[j + 1].value in ("=", ":="):
                s.next()
                names = []
                while not s.at("op", "]"):
                    names.append(s.expect("name").value)
                    if not s.accept("op", ","):
                        break
                s.expect("op", "]")
                s.next()
                return ("tdecl", names, self.rhs(ln, s))
        # typed declaration: float x = ..., series int y = ...
        if t.kind == "name" and t.value in TYPE_WORDS:
            k = 0
            while s.at("name", k=k) and s.peek(k).value in TYPE_WORDS:
                k += 1
            if s.at("op", "[", k):
                raise PineError("Array types are not supported yet", ln.line)
            if s.at("name", k=k) and s.at("op", "=", k + 1):
                name = self._decl_name(s)
                s.expect("op", "=")
                return ("decl", name, self.rhs(ln, s), False)
        if t.kind == "name" and s.at("op", k=1):
            op = s.peek(1).value
            if op == "=":
                name = s.next().value
                s.next()
                self._check_plain(name, ln.line)
                return ("decl", name, self.rhs(ln, s), False)
            if op == ":=":
                name = s.next().value
                s.next()
                return ("assign", name, self.rhs(ln, s))
            if op in ("+=", "-=", "*=", "/=", "%="):
                name = s.next().value
                s.next()
                return ("aug", name, op[0], self.rhs(ln, s))
        e = self.expr(s)
        s.end()
        return ("expr", e)

    @staticmethod
    def _check_plain(name: str, line: int) -> None:
        if "." in name:
            raise PineError(f"Cannot assign to '{name}'", line)

    def _decl_name(self, s: Stream) -> str:
        names = []
        while s.at("name"):
            names.append(s.next().value)
        if s.at("op", "["):
            raise PineError("Array types are not supported yet", s.line)
        if not names:
            raise PineError("Expected a variable name", s.line)
        self._check_plain(names[-1], s.line)
        return names[-1]

    @staticmethod
    def _match(s: Stream, k: int) -> int | None:
        """Index of the bracket matching the one at s.i + k."""
        open_ = s.toks[s.i + k].value
        close = {"(": ")", "[": "]"}[open_]
        depth = 0
        for j in range(s.i + k, len(s.toks)):
            t = s.toks[j]
            if t.kind == "op" and t.value == open_:
                depth += 1
            elif t.kind == "op" and t.value == close:
                depth -= 1
                if depth == 0:
                    return j
        return None

    def rhs(self, ln: Line, s: Stream):
        if s.accept("kw", "if"):
            return self.if_(ln, s, expr=True)
        if s.accept("kw", "switch"):
            return self.switch_(ln, s)
        e = self.expr(s)
        s.end()
        return e

    def if_(self, ln: Line, s: Stream, expr: bool):
        cond = self.expr(s)
        s.end()
        branches = [(cond, self.child(ln.indent, ln.line))]
        else_block = None
        while self.pos < len(self.lines):
            nxt = self.lines[self.pos]
            if nxt.indent != ln.indent or not (nxt.toks[0].kind == "kw" and nxt.toks[0].value == "else"):
                break
            self.pos += 1
            es = Stream(nxt.toks, nxt.line)
            es.next()
            if es.accept("kw", "if"):
                c = self.expr(es)
                es.end()
                branches.append((c, self.child(nxt.indent, nxt.line)))
            else:
                es.end()
                else_block = self.child(nxt.indent, nxt.line)
                break
        return ("ifx" if expr else "if", branches, else_block)

    def switch_(self, ln: Line, s: Stream):
        subject = None if s.done() else self.expr(s)
        s.end()
        if self.pos >= len(self.lines) or self.lines[self.pos].indent <= ln.indent:
            raise PineError("Expected switch cases", ln.line)
        ind = self.lines[self.pos].indent
        cases = []
        while self.pos < len(self.lines) and self.lines[self.pos].indent == ind:
            cl = self.lines[self.pos]
            self.pos += 1
            cs = Stream(cl.toks, cl.line)
            if cs.accept("op", "=>"):
                key = None
            else:
                key = self.expr(cs)
                cs.expect("op", "=>")
            if cs.done():
                body = self.child(cl.indent, cl.line)
            else:
                body = [(cl.line, self._statement(Line(cl.indent, cs.toks[cs.i:], cl.line),
                                                  Stream(cs.toks[cs.i:], cl.line)))]
            cases.append((key, body))
        return ("switchx", subject, cases)

    def for_(self, ln: Line, s: Stream):
        s.next()
        if s.at("op", "["):
            raise PineError("'for ... in' loops over arrays are not supported yet", ln.line)
        var = s.expect("name").value
        if s.accept("kw", "in"):
            raise PineError("'for ... in' loops over arrays are not supported yet", ln.line)
        s.expect("op", "=")
        start = self.expr(s)
        s.expect("kw", "to")
        end = self.expr(s)
        step = self.expr(s) if s.accept("kw", "by") else None
        s.end()
        return ("for", var, start, end, step, self.child(ln.indent, ln.line))

    def function(self, ln: Line, s: Stream, close_idx: int):
        name = s.next().value
        self._check_plain(name, ln.line)
        s.expect("op", "(")
        params = []
        while not s.at("op", ")"):
            words = []
            while s.at("name"):
                words.append(s.next().value)
            if not words:
                raise PineError("Expected a parameter name", ln.line)
            default = None
            if s.accept("op", "="):
                default = self.expr(s)
            params.append((words[-1], default))
            if not s.accept("op", ","):
                break
        s.expect("op", ")")
        s.expect("op", "=>")
        if s.done():
            body = self.child(ln.indent, ln.line)
        else:
            rest = Line(ln.indent, s.toks[s.i:], ln.line)
            body = [(ln.line, self._statement(rest, Stream(rest.toks, ln.line)))]
        return ("fn", name, params, body)

    # ------------------------------------------------------- expressions

    def expr(self, s: Stream):
        c = self._or(s)
        if s.accept("op", "?"):
            a = self.expr(s)
            s.expect("op", ":")
            b = self.expr(s)
            return ("tern", c, a, b)
        return c

    def _or(self, s):
        a = self._and(s)
        while s.accept("kw", "or"):
            a = ("or", a, self._and(s))
        return a

    def _and(self, s):
        a = self._eq(s)
        while s.accept("kw", "and"):
            a = ("and", a, self._eq(s))
        return a

    def _eq(self, s):
        a = self._cmp(s)
        while s.at("op") and s.peek().value in ("==", "!="):
            op = s.next().value
            a = ("bin", op, a, self._cmp(s))
        return a

    def _cmp(self, s):
        a = self._add(s)
        while s.at("op") and s.peek().value in ("<", ">", "<=", ">="):
            op = s.next().value
            a = ("bin", op, a, self._add(s))
        return a

    def _add(self, s):
        a = self._mul(s)
        while s.at("op") and s.peek().value in ("+", "-"):
            op = s.next().value
            a = ("bin", op, a, self._mul(s))
        return a

    def _mul(self, s):
        a = self._unary(s)
        while s.at("op") and s.peek().value in ("*", "/", "%"):
            op = s.next().value
            a = ("bin", op, a, self._unary(s))
        return a

    def _unary(self, s):
        if s.accept("op", "-"):
            return ("neg", self._unary(s))
        if s.accept("op", "+"):
            return self._unary(s)
        if s.accept("kw", "not"):
            return ("not", self._unary(s))
        return self._postfix(s)

    def _postfix(self, s):
        e = self._primary(s)
        while True:
            if s.at("op", "("):
                if e[0] != "name":
                    raise PineError("Only named functions can be called", s.line)
                e = self._call(e[1], s)
            elif s.at("op", "["):
                s.next()
                n = self.expr(s)
                s.expect("op", "]")
                e = ("hist", e, n)
            else:
                return e

    def _call(self, name: str, s: Stream):
        s.expect("op", "(")
        args, kwargs = [], {}
        while not s.at("op", ")"):
            if (s.at("name") or s.at("kw", "type")) and s.at("op", "=", 1):
                k = s.next().value
                s.next()
                kwargs[k] = self.expr(s)
            else:
                if kwargs:
                    raise PineError(f"Positional argument after keyword argument in {name}()", s.line)
                args.append(self.expr(s))
            if not s.accept("op", ","):
                break
        s.expect("op", ")")
        return ("call", name, args, kwargs)

    def _primary(self, s):
        t = s.next()
        if t.kind == "num":
            return ("num", t.value)
        if t.kind == "str":
            return ("str", t.value)
        if t.kind == "color":
            return ("na",)
        if t.kind == "kw":
            if t.value in ("true", "false"):
                return ("bool", t.value == "true")
            if t.value == "if":
                raise PineError("'if' expressions are only allowed on the right of '=' or ':='", t.line)
            raise PineError(f"Unexpected '{t.value}'", t.line)
        if t.kind == "name":
            if t.value == "na" and not s.at("op", "("):
                return ("na",)
            return ("name", t.value)
        if t.kind == "op" and t.value == "(":
            e = self.expr(s)
            s.expect("op", ")")
            return e
        if t.kind == "op" and t.value == "[":
            items = []
            while not s.at("op", "]"):
                items.append(self.expr(s))
                if not s.accept("op", ","):
                    break
            s.expect("op", "]")
            return ("tuple", items)
        raise PineError(f"Unexpected {t.value!r}", t.line)


def parse(source: str) -> tuple[list, int]:
    lines, version = tokenize(source)
    return Parser(lines).program(), version
