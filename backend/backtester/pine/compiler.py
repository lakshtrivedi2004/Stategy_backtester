"""Compile a Pine Script AST to a Python function that runs once per bar.

Only code built from the parsed AST is emitted: identifiers are checked against
a strict pattern and literals go through `repr`, so a script cannot inject
Python. The generated module looks like:

    def __build(R, S, F, T, NA):
        V_fast = R.var(False)            # one SeriesVar per script variable
        f_ta_ema = F['ta.ema']           # bound built-ins
        def U_myfunc(_cs, L_x): ...      # user functions (_cs = call-site key)
        def __bar():                     # the script body
            V_fast.cur = f_ta_ema('k1', R.close, 9)
        return __bar
"""
from __future__ import annotations

import re
import traceback
from dataclasses import dataclass, field
from typing import Any

from .parser import parse
from .runtime import (NA, STATELESS, STATEFUL, STATEFUL_VARS, STRATEGY_VARS, ORDER_PARAMS, PineError, Runtime,
                      T)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SERIES = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4", "time", "time_close",
          "bar_index", "year", "month", "dayofmonth", "dayofweek", "hour", "minute", "second", "weekofyear"}
DATE_FUNCS = {"year", "month", "dayofmonth", "dayofweek", "hour", "minute", "second", "weekofyear"}

BARSTATE = {
    "barstate.isfirst": "(R.i == 0)", "barstate.islast": "(R.i == R.last_bar_index)",
    "barstate.isconfirmed": "True", "barstate.ishistory": "True", "barstate.isrealtime": "False",
    "barstate.isnew": "True", "barstate.islastconfirmedhistory": "(R.i == R.last_bar_index)",
}
SYMINFO = {
    "syminfo.ticker": "R.symbol", "syminfo.tickerid": "R.symbol", "syminfo.root": "R.symbol",
    "syminfo.mintick": "R.mintick", "syminfo.pointvalue": "1.0", "syminfo.type": "R.sym_type",
    "syminfo.currency": "R.currency", "syminfo.basecurrency": "R.currency", "syminfo.timezone": "R.tz",
    "syminfo.description": "R.symbol", "syminfo.prefix": "''", "syminfo.session": "'regular'",
    "timeframe.period": "R.tf_period", "timeframe.main_period": "R.tf_period",
    "timeframe.multiplier": "(1 if R.bar_minutes >= 1440 else R.bar_minutes)",
    "timeframe.isintraday": "(R.bar_minutes < 1440)", "timeframe.isdaily": "(R.bar_minutes == 1440)",
    "timeframe.isminutes": "(R.bar_minutes < 1440)", "timeframe.isseconds": "False",
    "timeframe.isweekly": "False", "timeframe.ismonthly": "False", "timeframe.isdwm": "(R.bar_minutes >= 1440)",
    "timeframe.isticks": "False", "last_bar_index": "R.last_bar_index", "timenow": "R.timenow",
    "last_bar_time": "R.timenow", "math.pi": "3.141592653589793", "math.e": "2.718281828459045",
    "math.phi": "1.618033988749895", "math.rphi": "0.6180339887498949",
}
CONSTANTS = {
    "strategy.long": "long", "strategy.short": "short", "strategy.fixed": "fixed", "strategy.cash": "cash",
    "strategy.percent_of_equity": "percent_of_equity", "strategy.commission.percent": "percent",
    "strategy.commission.cash_per_contract": "cash_per_contract",
    "strategy.commission.cash_per_order": "cash_per_order", "strategy.direction.all": "all",
    "strategy.direction.long": "long", "strategy.direction.short": "short",
    "strategy.oca.none": "none", "strategy.oca.cancel": "cancel", "strategy.oca.reduce": "reduce",
    "barmerge.lookahead_on": "lookahead_on", "barmerge.lookahead_off": "lookahead_off",
    "barmerge.gaps_on": "gaps_on", "barmerge.gaps_off": "gaps_off",
    "dayofweek.sunday": 1, "dayofweek.monday": 2, "dayofweek.tuesday": 3, "dayofweek.wednesday": 4,
    "dayofweek.thursday": 5, "dayofweek.friday": 6, "dayofweek.saturday": 7,
}
# Namespaces whose members are display-only constants (colors, shapes, styles, ...).
CONST_NS = ("color.", "shape.", "location.", "size.", "plot.", "display.", "xloc.", "yloc.", "extend.",
            "position.", "text.", "font.", "format.", "scale.", "currency.", "alert.", "hline.", "line.",
            "label.", "box.", "table.", "order.", "adjustment.", "session.", "input.", "settlement_as_close.",
            "backadjustment.", "splits.", "dividends.", "earnings.")
# Display / alert functions: they don't affect trading, so they compile to na.
IGNORED_CALLS = {"plot", "plotshape", "plotchar", "plotarrow", "plotcandle", "plotbar", "bgcolor", "barcolor",
                 "fill", "hline", "alert", "alertcondition", "indicator", "study", "max_bars_back",
                 "runtime.log", "library"}
IGNORED_NS = ("label.", "line.", "box.", "table.", "linefill.", "polyline.", "log.", "color.", "chart.",
              "ticker.")
INPUT_FUNCS = {"input", "input.int", "input.float", "input.bool", "input.string", "input.source",
               "input.timeframe", "input.session", "input.symbol", "input.color", "input.price", "input.time",
               "input.text_area", "input.enum", "input.integer", "input.resolution"}
ORDER_FUNCS = set(ORDER_PARAMS)
STRATEGY_FUNCS = {"strategy.risk.allow_entry_in"}
# Pine v4 and earlier used bare names (sma, crossover, abs ...).
V4_ALIAS_NS = ("ta.", "math.", "str.", "request.")
V4_RENAMES = {"sum": "math.sum", "security": "request.security", "tostring": "str.tostring",
              "tonumber": "str.tonumber", "tr": "ta.tr", "vwap": "ta.vwap", "obv": "ta.obv",
              "valuewhen": "ta.valuewhen", "pivothigh": "ta.pivothigh", "pivotlow": "ta.pivotlow"}
PRICE_FIELDS = {"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4", "hlcc4"}


@dataclass
class Declaration:
    """Settings from the script's strategy(...) call."""
    title: str = "Pine strategy"
    initial_capital: float | None = None
    default_qty_type: str | None = None
    default_qty_value: float | None = None
    pyramiding: int = 0
    commission_type: str | None = None
    commission_value: float | None = None
    slippage: float | None = None
    process_orders_on_close: bool = False
    other: dict = field(default_factory=dict)


class Scope:
    def __init__(self, params: list[str]):
        self.locals = set(params)


class Compiler:
    def __init__(self, version: int):
        self.version = version
        self.body: list[tuple[int, str, int]] = []     # (indent, code, pine line)
        self.funcs: list[tuple[int, str, int]] = []
        self.out = self.body
        self.ind = 1
        self.gvars: dict[str, bool] = {}               # pine name -> persistent
        self.user_funcs: dict[str, list[str]] = {}
        self.bound: set[str] = set()
        self.scope: Scope | None = None
        self.n_key = 0
        self.n_tmp = 0
        self.line = 0
        self.declaration: Declaration | None = None
        self.kind = None                               # "strategy" | "indicator"
        self.warnings: set[str] = set()
        self.has_orders = False

    # ------------------------------------------------------------ helpers

    def emit(self, code: str) -> None:
        self.out.append((self.ind, code, self.line))

    def key(self) -> str:
        self.n_key += 1
        return repr(f"k{self.n_key}") if self.scope is None else f"_cs + '/k{self.n_key}'"

    def tmp(self) -> str:
        self.n_tmp += 1
        return f"_t{self.n_tmp}"

    def err(self, msg: str):
        raise PineError(msg, self.line)

    def ident(self, name: str) -> str:
        if not _IDENT.match(name):
            self.err(f"Invalid name '{name}'")
        return name

    def bind(self, name: str) -> str:
        py = "f_" + re.sub(r"[^A-Za-z0-9_]", "_", name)
        self.bound.add(name)
        return py

    # -------------------------------------------------------- statements

    def block(self, stmts: list) -> None:
        if not stmts:
            self.emit("pass")
        for line, st in stmts:
            self.line = line
            self.stmt(st)

    def block_value(self, stmts: list, target: str) -> None:
        """Compile a block whose last statement is its value (if/switch expressions, functions)."""
        self.emit(f"{target} = NA")
        if not stmts:
            return
        for line, st in stmts[:-1]:
            self.line = line
            self.stmt(st)
        line, last = stmts[-1]
        self.line = line
        kind = last[0]
        if kind == "expr":
            self.emit(f"{target} = {self.expr(last[1])}")
        elif kind in ("decl", "assign", "aug"):
            self.stmt(last)
            self.emit(f"{target} = {self.ref(last[1])}")
        elif kind == "tdecl":
            self.stmt(last)
            self.emit(f"{target} = ({', '.join(self.ref(n) for n in last[1] if n != '_')},)")
        elif kind == "if":
            self.emit(f"{target} = {self.expr(('ifx', last[1], last[2]))}")
        else:
            self.stmt(last)

    def target(self, name: str, declare: bool, persistent: bool = False) -> str:
        """Python lvalue for assigning a script variable."""
        self.ident(name)
        if self.scope is not None:
            if declare:
                if persistent:
                    self.err("'var' declarations inside functions are not supported yet")
                self.scope.locals.add(name)
                return f"L_{name}"
            if name in self.scope.locals:
                return f"L_{name}"
            if name in self.gvars:
                self.err(f"Cannot modify global variable '{name}' from inside a function")
            self.err(f"Undeclared variable '{name}'")
        if declare:
            if name in self.gvars and self.gvars[name] != persistent:
                self.gvars[name] = self.gvars[name] or persistent
            else:
                self.gvars.setdefault(name, persistent)
            return f"V_{name}.cur"
        if name not in self.gvars:
            self.err(f"Undeclared variable '{name}' (declare it with '=' before using ':=')")
        return f"V_{name}.cur"

    def ref(self, name: str) -> str:
        if self.scope is not None and name in self.scope.locals:
            return f"L_{name}"
        return f"V_{name}.cur"

    def stmt(self, st) -> None:
        kind = st[0]
        if kind == "decl":
            _, name, e, persistent = st
            if self.scope is None and name in self.user_funcs:
                self.err(f"'{name}' is already a function")
            if persistent and self.scope is None:
                self.ident(name)
                self.gvars[name] = True
                self.emit(f"if V_{name}.fresh:")
                self.ind += 1
                self.emit(f"V_{name}.fresh = False")
                self.emit(f"V_{name}.cur = {self.expr(e)}")
                self.ind -= 1
                return
            value = self.expr(e)
            self.emit(f"{self.target(name, True, persistent)} = {value}")
        elif kind == "assign":
            _, name, e = st
            value = self.expr(e)
            self.emit(f"{self.target(name, False)} = {value}")
        elif kind == "aug":
            _, name, op, e = st
            value = self.expr(e)
            tgt = self.target(name, False)
            rhs = f"f_div({tgt}, {value})" if op == "/" else f"f_mod({tgt}, {value})" if op == "%" \
                else f"({tgt} {op} {value})"
            self.bound.update({"__div", "__mod"})
            self.emit(f"{tgt} = {rhs}")
        elif kind == "tdecl":
            _, names, e = st
            value = self.expr(e)
            t = self.tmp()
            self.emit(f"{t} = {value}")
            self.emit(f"if not isinstance({t}, tuple): {t} = ({t},) * {len(names)}")
            for k, n in enumerate(names):
                if n == "_":
                    continue
                self.emit(f"{self.target(n, True)} = {t}[{k}] if len({t}) > {k} else NA")
        elif kind == "expr":
            e = st[1]
            if e[0] == "call" and e[1] in ("strategy", "indicator", "study", "library"):
                if self.scope is not None:
                    self.err(f"{e[1]}() must be at the top level of the script")
                self.declare(e)
                return
            code = self.expr(e)
            if code not in ("NA", "None"):
                self.emit(code)
        elif kind == "if":
            _, branches, else_block = st
            for k, (cond, blk) in enumerate(branches):
                c = self.expr(cond)
                self.emit(f"{'if' if k == 0 else 'elif'} T({c}):")
                self.ind += 1
                self.block(blk)
                self.ind -= 1
                if k + 1 < len(branches):
                    # later conditions may need set-up statements; nest them under an else
                    self.emit("else:")
                    self.ind += 1
                    self.stmt(("if", branches[k + 1:], else_block))
                    self.ind -= 1
                    return
            if else_block is not None:
                self.emit("else:")
                self.ind += 1
                self.block(else_block)
                self.ind -= 1
        elif kind == "for":
            _, var, start, end, step, blk = st
            a, b = self.expr(start), self.expr(end)
            s = self.expr(step) if step is not None else "None"
            it = self.tmp()
            self.emit(f"for {it} in R.rng({a}, {b}, {s}):")
            self.ind += 1
            self.emit(f"{self.target(var, True)} = {it}")
            self.block(blk)
            self.ind -= 1
        elif kind == "while":
            _, cond, blk = st
            g = self.tmp()
            self.emit(f"{g} = 0")
            self.emit(f"while T({self.expr(cond)}):")
            self.ind += 1
            self.emit(f"{g} += 1")
            self.emit(f"if {g} > 100000: R.loop_guard()")
            self.block(blk)
            self.ind -= 1
        elif kind in ("break", "continue"):
            self.emit(kind)
        elif kind == "fn":
            self.function(st)
        else:  # pragma: no cover
            self.err(f"Unsupported statement {kind}")

    def function(self, st) -> None:
        _, name, params, body = st
        if self.scope is not None:
            self.err("Functions must be defined at the top level")
        self.ident(name)
        names = [self.ident(p) for p, _ in params]
        defaults = []
        for p, d in params:
            defaults.append(f"L_{p}={self.expr(d)}" if d is not None else f"L_{p}")
        self.user_funcs[name] = names
        saved_out, saved_ind = self.out, self.ind
        self.out, self.ind = self.funcs, 1
        self.emit(f"def U_{name}(_cs, {', '.join(defaults)}):" if defaults else f"def U_{name}(_cs):")
        self.ind += 1
        self.scope = Scope(names)
        result = self.tmp()
        self.block_value(body, result)
        self.emit(f"return {result}")
        self.scope = None
        self.out, self.ind = saved_out, saved_ind

    def declare(self, call) -> None:
        _, name, args, kwargs = call
        self.kind = "strategy" if name == "strategy" else "indicator"
        if name != "strategy":
            return
        if self.declaration is not None:
            self.err("strategy() is declared twice")
        positional = ["title", "shorttitle", "overlay", "format", "precision", "scale", "pyramiding",
                      "calc_on_order_fills", "calc_on_every_tick", "max_bars_back", "backtest_fill_limits_assumption",
                      "default_qty_type", "default_qty_value", "initial_capital", "currency", "slippage",
                      "commission_type", "commission_value", "process_orders_on_close"]
        vals = {positional[k]: self.const(a) for k, a in enumerate(args) if k < len(positional)}
        vals.update({k: self.const(v) for k, v in kwargs.items()})
        d = Declaration()
        d.title = str(vals.get("title") or "Pine strategy")
        num = lambda k: float(vals[k]) if isinstance(vals.get(k), (int, float)) else None
        d.initial_capital = num("initial_capital")
        d.default_qty_type = vals.get("default_qty_type") if isinstance(vals.get("default_qty_type"), str) else None
        d.default_qty_value = num("default_qty_value")
        d.pyramiding = int(num("pyramiding") or 0)
        d.commission_type = vals.get("commission_type") if isinstance(vals.get("commission_type"), str) else None
        d.commission_value = num("commission_value")
        d.slippage = num("slippage")
        d.process_orders_on_close = bool(vals.get("process_orders_on_close") or False)
        self.declaration = d

    def const(self, e) -> Any:
        """Value of a constant expression in strategy(...)."""
        k = e[0]
        if k in ("num", "str", "bool"):
            return e[1]
        if k == "na":
            return None
        if k == "neg":
            v = self.const(e[1])
            return -v if isinstance(v, (int, float)) else None
        if k == "name":
            return CONSTANTS.get(e[1], e[1])
        if k == "bin":
            a, b = self.const(e[2]), self.const(e[3])
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                return {"+": a + b, "-": a - b, "*": a * b, "/": a / b if b else None}.get(e[1])
        if k == "call" and e[1] in INPUT_FUNCS:
            return self.const(e[3].get("defval", e[2][0])) if (e[2] or "defval" in e[3]) else None
        return None

    # ------------------------------------------------------- expressions

    def expr(self, e) -> str:
        k = e[0]
        if k == "num":
            return repr(e[1])
        if k == "str":
            return repr(e[1])
        if k == "bool":
            return "True" if e[1] else "False"
        if k == "na":
            return "NA"
        if k == "name":
            return self.name(e[1])
        if k == "hist":
            return self.history(e[1], e[2])
        if k == "call":
            return self.call(e[1], e[2], e[3])
        if k == "bin":
            op, a, b = e[1], self.expr(e[2]), self.expr(e[3])
            if op == "/":
                self.bound.add("__div")
                return f"f_div({a}, {b})"
            if op == "%":
                self.bound.add("__mod")
                return f"f_mod({a}, {b})"
            return f"({a} {op} {b})"
        if k == "and":
            return f"(T({self.expr(e[1])}) and T({self.expr(e[2])}))"
        if k == "or":
            return f"(T({self.expr(e[1])}) or T({self.expr(e[2])}))"
        if k == "not":
            return f"(not T({self.expr(e[1])}))"
        if k == "neg":
            return f"(-{self.expr(e[1])})"
        if k == "tern":
            return f"({self.expr(e[2])} if T({self.expr(e[1])}) else {self.expr(e[3])})"
        if k == "tuple":
            return "(" + ", ".join(self.expr(x) for x in e[1]) + ",)"
        if k == "ifx":
            t = self.tmp()
            self.emit(f"{t} = NA")
            self._ifx(e[1], e[2], t)
            return t
        if k == "switchx":
            return self.switch(e)
        self.err(f"Unsupported expression {k}")

    def _ifx(self, branches, else_block, t) -> None:
        cond, blk = branches[0]
        self.emit(f"if T({self.expr(cond)}):")
        self.ind += 1
        self.block_value(blk, t)
        self.ind -= 1
        if len(branches) > 1 or else_block is not None:
            self.emit("else:")
            self.ind += 1
            if len(branches) > 1:
                self._ifx(branches[1:], else_block, t)
            else:
                self.block_value(else_block, t)
            self.ind -= 1

    def switch(self, e) -> str:
        _, subject, cases = e
        t = self.tmp()
        self.emit(f"{t} = NA")
        subj = None
        if subject is not None:
            subj = self.tmp()
            self.emit(f"{subj} = {self.expr(subject)}")
        first = True
        default = None
        for key, body in cases:
            if key is None:
                default = body
                continue
            c = f"({subj} == {self.expr(key)})" if subj else f"T({self.expr(key)})"
            if first:
                self.emit(f"if {c}:")
            else:
                self.emit("else:")
                self.ind += 1
                self.emit(f"if {c}:")
            self.ind += 1
            self.block_value(body, t)
            self.ind -= 1
            first = False
        if default is not None:
            if first:
                self.block_value(default, t)
            else:
                self.emit("else:")
                self.ind += 1
                self.block_value(default, t)
                self.ind -= 1
        # close the nested else: levels opened for cases after the first
        n_cases = sum(1 for key, _ in cases if key is not None)
        self.ind -= max(0, n_cases - 1)
        return t

    def resolve(self, name: str) -> str:
        """Map v4-style bare names to their v5 namespaces."""
        if name in V4_RENAMES and name not in self.gvars and not (self.scope and name in self.scope.locals):
            return V4_RENAMES[name]
        return name

    def name(self, name: str) -> str:
        if self.scope is not None and name in self.scope.locals:
            return f"L_{name}"
        if name in self.gvars:
            return f"V_{name}.cur"
        name = self.resolve(name) if name not in SERIES else name
        if name in SERIES:
            return f"R.{name}"
        if name in BARSTATE:
            return BARSTATE[name]
        if name in SYMINFO:
            return SYMINFO[name]
        if name in CONSTANTS:
            return repr(CONSTANTS[name])
        if name.startswith("strategy.") and name[9:] in STRATEGY_VARS:
            return f"S.{name[9:]}"
        if name in STATEFUL_VARS:
            meth, arg = STATEFUL_VARS[name]
            return f"R.{meth}({self.key()}{', ' + arg if arg else ''})"
        if name.startswith(CONST_NS):
            return repr(name)
        if name in self.user_funcs:
            self.err(f"'{name}' is a function; call it with ()")
        self.err(f"Unknown identifier '{name}'")

    def history(self, base, n) -> str:
        nn = self.expr(n)
        if base[0] == "name":
            nm = base[1]
            if self.scope is not None and nm in self.scope.locals:
                return f"R.hist({self.key()}, L_{nm}, {nn})"
            if nm in self.gvars:
                return f"V_{nm}.get({nn})"
            if nm in SERIES:
                return f"R.h({nm!r}, {nn})"
        return f"R.hist({self.key()}, {self.expr(base)}, {nn})"

    def args(self, args, kwargs) -> str:
        parts = [self.expr(a) for a in args]
        for k, v in kwargs.items():
            parts.append(f"{self.ident(k)}={self.expr(v)}")
        return ", ".join(parts)

    def call(self, name: str, args: list, kwargs: dict) -> str:
        if name in self.user_funcs:
            params = self.user_funcs[name]
            if len(args) > len(params):
                self.err(f"{name}() takes {len(params)} arguments")
            parts = [self.key()] + [self.expr(a) for a in args]
            for k, v in kwargs.items():
                if k not in params:
                    self.err(f"{name}() has no parameter '{k}'")
                parts.append(f"L_{k}={self.expr(v)}")
            return f"U_{name}({', '.join(parts)})"
        orig = name
        if name not in STATELESS and name not in STATEFUL and name not in INPUT_FUNCS \
                and name not in ORDER_FUNCS and name not in DATE_FUNCS and name != "time":
            name = self.resolve(name)
            if name == orig and "." not in name:
                for ns in V4_ALIAS_NS:
                    if ns + name in STATEFUL or ns + name in STATELESS:
                        name = ns + name
                        break
        if name in IGNORED_CALLS or name.startswith(IGNORED_NS):
            return "NA"
        if name in INPUT_FUNCS:
            dv = kwargs.get("defval", args[0] if args else ("na",))
            title = kwargs.get("title", args[1] if len(args) > 1 else None)
            t = repr(title[1]) if title is not None and title[0] == "str" else "None"
            label = f", {dv[1]!r}" if dv[0] == "name" else ""
            return f"R.inp({t}, {self.expr(dv)}{label})"
        if name in ORDER_FUNCS:
            self.has_orders = True
            return f"{self.bind(name)}({self.args(args, kwargs)})"
        if name == "strategy.risk.allow_entry_in":
            return f"R.allow_entry_in({self.args(args, kwargs)})"
        if name.startswith("strategy.risk."):
            self.warnings.add(f"{name}() is ignored")
            return "NA"
        if name.startswith(("strategy.closedtrades.", "strategy.opentrades.")):
            ns, attr = name.rsplit(".", 1)
            meth = "closed" if ns.endswith("closedtrades") else "opened"
            return f"R.{meth}({attr!r})({self.args(args, kwargs)})"
        if name == "request.security":
            return self.security(args, kwargs)
        if name.startswith("request."):
            self.err(f"{name}() is not supported (only request.security is)")
        if name == "time" or name == "time_close":
            return f"R.time_fn({self.args(args, kwargs)})"
        if name in DATE_FUNCS:
            return f"R.date_part({name!r})({self.args(args, kwargs)})"
        if name == "timestamp":
            return f"R.timestamp({self.args(args, kwargs)})"
        if name == "runtime.error":
            return f"R.raise_error({self.args(args, kwargs)})"
        if name in STATEFUL:
            meth = STATEFUL[name]
            a = self.args(args, kwargs)
            return f"R.{meth}({self.key()}{', ' + a if a else ''})"
        if name in STATELESS:
            return f"{self.bind(name)}({self.args(args, kwargs)})"
        self.err(f"Function '{orig}' is not supported")

    def security(self, args, kwargs) -> str:
        names = ["symbol", "timeframe", "expression", "gaps", "lookahead"]
        bound = dict(zip(names, args))
        bound.update(kwargs)
        if "expression" not in bound:
            self.err("request.security() needs an expression")
        e = bound["expression"]
        tf = self.expr(bound.get("timeframe", ("str", "")))
        look = bound.get("lookahead")
        look_on = look is not None and look[0] == "name" and look[1] == "barmerge.lookahead_on"
        sym = bound.get("symbol")
        if sym is not None and not (sym[0] == "name" and sym[1] in ("syminfo.tickerid", "syminfo.ticker")):
            self.warnings.add("request.security() with another symbol uses the tested instrument's own data")
        field, n = None, ("num", 0)
        if e[0] == "name" and e[1] in PRICE_FIELDS:
            field = e[1]
        elif e[0] == "hist" and e[1][0] == "name" and e[1][1] in PRICE_FIELDS:
            field, n = e[1][1], e[2]
        if field is None:
            self.warnings.add("request.security() with a calculated expression is evaluated on the chart "
                              "timeframe; only plain price series (close, high[1], ...) use the higher timeframe")
            return self.expr(e)
        if look_on and n == ("num", 0):
            self.warnings.add("request.security() with lookahead_on and no [1] offset reads the future "
                              "(the higher-timeframe bar's final value)")
        return f"R.security({tf}, {field!r}, {self.expr(n)}, {look_on})"

    # ------------------------------------------------------------ output

    def module(self) -> str:
        lines = ["def __build(R, S, F, T, NA):"]
        for name, persistent in self.gvars.items():
            lines.append(f"    V_{name} = R.var({persistent})")
        lines.append("    f_div = F['__div']")
        lines.append("    f_mod = F['__mod']")
        for name in sorted(self.bound - {"__div", "__mod"}):
            py = "f_" + re.sub(r"[^A-Za-z0-9_]", "_", name)
            lines.append(f"    {py} = F[{name!r}]")
        header = len(lines)
        self.line_map = [0] * header
        for ind, code, line in self.funcs:
            lines.append("    " * ind + code)
            self.line_map.append(line)
        lines.append("    def __bar():")
        self.line_map.append(0)
        if not self.body:
            lines.append("        pass")
            self.line_map.append(0)
        for ind, code, line in self.body:
            lines.append("    " * (ind + 1) + code)
            self.line_map.append(line)
        lines.append("    return __bar")
        self.line_map.append(0)
        return "\n".join(lines)


@dataclass
class CompiledScript:
    source: str
    python: str
    declaration: Declaration
    version: int
    warnings: list[str]
    line_map: list[int]
    code: Any
    var_names: list[str] = field(default_factory=list)   # in the order the runtime creates them

    def bind(self, runtime: Runtime):
        from .runtime import _div, _mod
        F = {"__div": _div, "__mod": _mod}
        F.update(STATELESS)
        for name in ORDER_PARAMS:
            F[name] = runtime.order_fn(name)
        ns: dict = {"__builtins__": {"isinstance": isinstance, "tuple": tuple, "len": len}}
        exec(self.code, ns)
        return ns["__build"](runtime, runtime.S, F, T, NA)

    def pine_line(self, exc: BaseException) -> int | None:
        for frame, lineno in reversed(list(traceback.walk_tb(exc.__traceback__))):
            if frame.f_code.co_filename == "<pine>":
                idx = lineno - 1
                if 0 <= idx < len(self.line_map) and self.line_map[idx]:
                    return self.line_map[idx]
        return None


def compile_pine(source: str) -> CompiledScript:
    ast, version = parse(source)
    c = Compiler(version or 5)
    c.block(ast)
    if c.kind == "indicator" or (c.declaration is None and not c.has_orders):
        raise PineError("This is a Pine indicator, not a strategy. Declare it with strategy(...) and place "
                        "orders with strategy.entry / strategy.exit / strategy.close.")
    if not c.has_orders:
        raise PineError("The script never places an order (strategy.entry, strategy.order, strategy.close ...)")
    if c.declaration is None:
        c.declaration = Declaration()
    py = c.module()
    try:
        code = compile(py, "<pine>", "exec")
    except SyntaxError as exc:  # pragma: no cover - a compiler bug, not a script error
        raise PineError(f"Internal compiler error: {exc}") from exc
    return CompiledScript(source, py, c.declaration, version or 5, sorted(c.warnings), c.line_map, code,
                          list(c.gvars))
