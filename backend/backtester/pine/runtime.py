"""Runtime for compiled Pine Script: series storage, built-in variables and functions.

Compiled scripts call these functions once per bar. Stateful functions (`ta.*`,
`math.sum`, ...) get a call-site key, so every call in the script keeps its own
history, as in Pine. A function's history only advances on bars where that call
actually runs, which matches Pine's behaviour for calls inside `if` blocks.
"""
from __future__ import annotations

import math
import random as _random
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from ..engine.broker import Broker

NA = float("nan")


class PineError(Exception):
    """A problem in the user's script (compile or run time), with its Pine line number."""

    def __init__(self, message: str, line: int | None = None):
        self.message, self.line = message, line
        super().__init__(f"Line {line}: {message}" if line else message)


def na(x) -> bool:
    return x is None or x != x


def T(x) -> bool:
    """Pine truthiness: na is false."""
    return bool(x) and x == x


def nz(x, replacement=0.0):
    return replacement if na(x) else x


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return NA


class SeriesVar:
    """A script variable. `hist` holds its committed value on every earlier bar."""
    __slots__ = ("cur", "hist", "persistent", "fresh")

    def __init__(self, persistent: bool):
        self.cur: Any = NA
        self.hist: list = []
        self.persistent = persistent
        self.fresh = True

    def get(self, n):
        n = int(n) if n == n else 0
        if n <= 0:
            return self.cur
        return self.hist[-n] if n <= len(self.hist) else NA


class StrategyView:
    """`strategy.*` variables."""

    def __init__(self, broker: Broker, currency: str):
        self.b, self.account_currency = broker, currency

    position_size = property(lambda s: s.b.position_size)
    position_avg_price = property(lambda s: s.b.position_avg_price)
    position_entry_name = property(lambda s: s.b.position_entry_name)
    equity = property(lambda s: s.b.equity)
    netprofit = property(lambda s: s.b.netprofit)
    openprofit = property(lambda s: s.b.openprofit)
    initial_capital = property(lambda s: s.b.cfg.initial_capital)
    closedtrades = property(lambda s: len(s.b.trades))
    opentrades = property(lambda s: len(s.b.lots))
    wintrades = property(lambda s: sum(1 for t in s.b.trades if t.pnl > 0))
    losstrades = property(lambda s: sum(1 for t in s.b.trades if t.pnl < 0))
    eventrades = property(lambda s: sum(1 for t in s.b.trades if t.pnl == 0))
    grossprofit = property(lambda s: sum(t.pnl for t in s.b.trades if t.pnl > 0))
    grossloss = property(lambda s: -sum(t.pnl for t in s.b.trades if t.pnl < 0))
    max_drawdown = property(lambda s: s.b.max_drawdown)


STRATEGY_VARS = {n for n, v in vars(StrategyView).items() if isinstance(v, property)} | {"account_currency"}

# Positional parameter order of the order functions (v5/v6, and v4 where it differs).
ORDER_PARAMS = {
    "strategy.entry": ["id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "alert_message"],
    "strategy.order": ["id", "direction", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "alert_message"],
    "strategy.exit": ["id", "from_entry", "qty", "qty_percent", "profit", "limit", "loss", "stop", "trail_price",
                      "trail_points", "trail_offset", "oca_name", "comment"],
    "strategy.close": ["id", "comment", "qty", "qty_percent", "alert_message", "immediately"],
    "strategy.close_all": ["comment", "alert_message", "immediately"],
    "strategy.cancel": ["id"],
    "strategy.cancel_all": [],
}
ORDER_PARAMS_V4 = {
    **ORDER_PARAMS,
    "strategy.entry": ["id", "long", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "when"],
    "strategy.order": ["id", "long", "qty", "limit", "stop", "oca_name", "oca_type", "comment", "when"],
    "strategy.exit": ["id", "from_entry", "qty", "qty_percent", "profit", "limit", "loss", "stop", "trail_price",
                      "trail_points", "trail_offset", "oca_name", "comment", "when"],
    "strategy.close": ["id", "when", "comment", "qty", "qty_percent"],
    "strategy.close_all": ["when", "comment"],
    "strategy.cancel": ["id", "when"],
    "strategy.cancel_all": ["when"],
}


def _period_ids(index: pd.DatetimeIndex, tf: str, session_open: tuple[int, int] | None) -> np.ndarray | None:
    """Integer id of the higher-timeframe bar each chart bar belongs to (None = unknown tf)."""
    tf = (tf or "").strip().upper()
    if not tf:
        return None
    num = "".join(ch for ch in tf if ch.isdigit())
    unit = tf[len(num):] or "MIN"
    n = int(num) if num else 1
    local = index.tz_localize(None) if index.tz is not None else index
    if unit == "MIN":
        minutes = (local.hour * 60 + local.minute).to_numpy()
        days = local.normalize().as_unit("s").asi8 // 86_400
        anchor = session_open[0] * 60 + session_open[1] if session_open else 0
        return days * 10_000 + (minutes - anchor) // n
    if unit == "D":
        return (local.normalize().as_unit("s").asi8 // 86_400) // n
    if unit == "W":
        iso = local.isocalendar()
        return (iso["year"].to_numpy() * 100 + iso["week"].to_numpy()) // n
    if unit == "M":
        return local.year.to_numpy() * 12 + (local.month.to_numpy() - 1) // n
    if unit == "S":
        return None
    return None


def tf_seconds(tf: str) -> int:
    tf = (tf or "").strip().upper()
    num = "".join(ch for ch in tf if ch.isdigit())
    unit = tf[len(num):]
    n = int(num) if num else 1
    return n * {"": 60, "S": 1, "D": 86400, "W": 604800, "M": 2_628_003}.get(unit, 60)


class Runtime:
    def __init__(self, candles: pd.DataFrame, broker: Broker, *, symbol: str, mintick: float, sym_type: str,
                 currency: str, timezone: str, bar_minutes: int, session_open=None, version: int = 5):
        self.broker = broker
        self.S = StrategyView(broker, currency)
        self.version = version
        self.n = len(candles)
        self.i = 0
        self.bufs: dict[str, list] = {}
        self.state: dict[str, dict] = {}
        self.vars: list[SeriesVar] = []
        self.inputs: dict[str, Any] = {}
        self.warnings: set[str] = set()
        self.index = candles.index
        self.session_open = session_open
        self.bar_minutes = bar_minutes
        self.tz = timezone
        self.symbol, self.mintick, self.sym_type, self.currency = symbol, mintick, sym_type, currency

        o, h, l, c, v = (candles[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close", "volume"))
        t = candles.index.as_unit("ms").asi8.astype(np.int64)
        local = candles.index
        cols = {
            "open": o, "high": h, "low": l, "close": c, "volume": v,
            "hl2": (h + l) / 2, "hlc3": (h + l + c) / 3, "ohlc4": (o + h + l + c) / 4, "hlcc4": (h + l + 2 * c) / 4,
            "time": t, "time_close": t + bar_minutes * 60_000, "bar_index": np.arange(self.n),
            "year": local.year.to_numpy(), "month": local.month.to_numpy(), "dayofmonth": local.day.to_numpy(),
            "dayofweek": (local.dayofweek.to_numpy() + 1) % 7 + 1, "hour": local.hour.to_numpy(),
            "minute": local.minute.to_numpy(), "second": local.second.to_numpy(),
            "weekofyear": local.isocalendar()["week"].to_numpy(),
        }
        self.arrays = cols
        self.cols = {k: arr.tolist() for k, arr in cols.items()}
        self.used: list[str] = list(self.cols)
        self.last_bar_index = self.n - 1
        self.timenow = int(t[-1]) if self.n else 0
        self.tf_period = "D" if bar_minutes >= 1440 else str(bar_minutes)
        self._htf_cache: dict[str, tuple] = {}

    # ------------------------------------------------------------ series

    def var(self, persistent: bool) -> SeriesVar:
        v = SeriesVar(persistent)
        self.vars.append(v)
        return v

    def begin(self, i: int) -> None:
        self.i = i
        cols = self.cols
        for name in self.used:
            setattr(self, name, cols[name][i])
        for v in self.vars:
            if not v.persistent:
                v.cur = NA

    def commit(self) -> None:
        for v in self.vars:
            v.hist.append(v.cur)

    def h(self, name: str, n) -> Any:
        """History of a built-in series, e.g. close[1]."""
        n = int(n) if n == n else 0
        j = self.i - n
        return self.cols[name][j] if 0 <= j < self.n else NA

    def push(self, key: str, value) -> list:
        """Record `value` for this call site on this bar; returns the call site's history."""
        b = self.bufs.get(key)
        if b is None:
            b = self.bufs[key] = [-1]
        if b[0] == self.i:
            b[-1] = value
        else:
            b.append(value)
            b[0] = self.i
        return b

    def prev(self, key: str, default=NA):
        """This call site's value from its previous execution (not this bar's)."""
        b = self.bufs.get(key)
        if b is None:
            return default
        if b[0] == self.i:
            return b[-2] if len(b) >= 3 else default
        return b[-1] if len(b) >= 2 else default

    def hist(self, key: str, value, n):
        b = self.push(key, value)
        n = int(n) if n == n else 0
        return b[-1 - n] if n < len(b) - 1 else NA

    def st(self, key: str) -> dict:
        """State for recursive functions. `p` = previous bar's result, `c` = this bar's."""
        s = self.state.get(key)
        if s is None:
            s = self.state[key] = {"bar": -1, "p": None, "c": None}
        if s["bar"] != self.i:
            s["p"], s["bar"] = s["c"], self.i
        return s

    def inp(self, title, value, label=None):
        if title and title not in self.inputs:
            ok = isinstance(value, (int, float, str, bool)) and value == value
            self.inputs[str(title)] = label if label is not None else (value if ok else None)
        return value

    def raise_error(self, message="", *_, **__):
        raise PineError(f"runtime.error: {message}")

    def rng(self, a, b, step=None):
        if na(a) or na(b):
            return ()
        if step is None or na(step) or step == 0:
            step = 1 if b >= a else -1
        else:
            step = abs(step) if b >= a else -abs(step)
        if all(isinstance(x, int) or float(x).is_integer() for x in (a, b, step)):
            r = range(int(a), int(b) + (1 if step > 0 else -1), int(step))
        else:
            r, x = [], a
            while (x <= b) if step > 0 else (x >= b):
                r.append(x)
                x += step
        if len(r) > 100_000:
            raise PineError("A for loop ran more than 100,000 iterations on one bar.")
        return r

    def loop_guard(self):
        raise PineError("A while loop ran more than 100,000 iterations on one bar.")

    # --------------------------------------------------------- window helpers

    def _window(self, key: str, source, length):
        n = int(length) if not na(length) else 0
        b = self.push(key, source)
        if n <= 0 or len(b) - 1 < n:
            return None
        return b[len(b) - n:]

    def sma(self, key, source, length):
        w = self._window(key, source, length)
        if w is None:
            return NA
        s = sum(w)
        return s / len(w)

    def _ema(self, key, source, length, alpha):
        n = int(length)
        seed_w = self._window(key + "s", source, n)
        prev = self.prev(key)
        if na(prev):
            v = NA if seed_w is None else sum(seed_w) / n
        else:
            v = alpha * source + (1 - alpha) * prev
        self.push(key, v)
        return v

    def ema(self, key, source, length):
        return self._ema(key, source, length, 2.0 / (int(length) + 1))

    def rma(self, key, source, length):
        return self._ema(key, source, length, 1.0 / int(length))

    def wma(self, key, source, length):
        w = self._window(key, source, length)
        if w is None:
            return NA
        n = len(w)
        return sum(x * (k + 1) for k, x in enumerate(w)) / (n * (n + 1) / 2)

    def vwma(self, key, source, length):
        a = self.sma(key + "a", source * self.volume, length)
        b = self.sma(key + "b", self.volume, length)
        return a / b if b else NA

    def hma(self, key, source, length):
        n = int(length)
        half = self.wma(key + "h", source, max(1, n // 2))
        full = self.wma(key + "f", source, n)
        return self.wma(key + "o", 2 * half - full, max(1, int(math.floor(math.sqrt(n)))))

    def change(self, key, source, length=1):
        b = self.push(key, source)
        n = int(length)
        return source - b[-1 - n] if len(b) - 1 > n else NA

    def tr(self, key=None, handle_na=False):
        pc = self.h("close", 1)
        if na(pc):
            return self.high - self.low if handle_na else NA
        return max(self.high - self.low, abs(self.high - pc), abs(self.low - pc))

    def atr(self, key, length):
        return self.rma(key, self.tr(None, True), length)

    def rsi(self, key, source, length):
        p = self.prev(key + "x")
        self.push(key + "x", source)
        if na(p) or na(source):
            u = d = NA
        else:
            u, d = max(source - p, 0.0), max(p - source, 0.0)
        up = self.rma(key + "u", u, length)
        dn = self.rma(key + "d", d, length)
        if na(up) or na(dn):
            return NA
        if dn == 0:
            return 100.0
        if up == 0:
            return 0.0
        return 100.0 - 100.0 / (1.0 + up / dn)

    def stdev(self, key, source, length, biased=True):
        w = self._window(key, source, length)
        if w is None:
            return NA
        m = sum(w) / len(w)
        d = len(w) if T(biased) else max(1, len(w) - 1)
        return math.sqrt(sum((x - m) ** 2 for x in w) / d)

    def variance(self, key, source, length, biased=True):
        s = self.stdev(key, source, length, biased)
        return s * s

    def dev(self, key, source, length):
        w = self._window(key, source, length)
        if w is None:
            return NA
        m = sum(w) / len(w)
        return sum(abs(x - m) for x in w) / len(w)

    def _hl_args(self, args, default_src):
        if len(args) == 1:
            return default_src, args[0]
        return args[0], args[1]

    def highest(self, key, *args, source=None, length=None):
        if length is not None:
            args = (args[0] if args else (source if source is not None else self.high), length)
        elif source is not None:
            args = (source,) + args
        src, n = self._hl_args(args, self.high)
        w = self._window(key, src, n)
        if w is None:
            return NA
        w = [x for x in w if not na(x)]
        return max(w) if w else NA

    def lowest(self, key, *args, source=None, length=None):
        if length is not None:
            args = (args[0] if args else (source if source is not None else self.low), length)
        elif source is not None:
            args = (source,) + args
        src, n = self._hl_args(args, self.low)
        w = self._window(key, src, n)
        if w is None:
            return NA
        w = [x for x in w if not na(x)]
        return min(w) if w else NA

    def highestbars(self, key, *args):
        src, n = self._hl_args(args, self.high)
        w = self._window(key, src, n)
        if w is None:
            return NA
        k = max(range(len(w)), key=lambda j: (w[j], j))
        return -(len(w) - 1 - k)

    def lowestbars(self, key, *args):
        src, n = self._hl_args(args, self.low)
        w = self._window(key, src, n)
        if w is None:
            return NA
        k = min(range(len(w)), key=lambda j: (w[j], -j))
        return -(len(w) - 1 - k)

    def crossover(self, key, source1, source2):
        pa, pb = self.prev(key + "a"), self.prev(key + "b")
        self.push(key + "a", source1)
        self.push(key + "b", source2)
        return T(source1 > source2) and T(pa <= pb)

    def crossunder(self, key, source1, source2):
        pa, pb = self.prev(key + "a"), self.prev(key + "b")
        self.push(key + "a", source1)
        self.push(key + "b", source2)
        return T(source1 < source2) and T(pa >= pb)

    def cross(self, key, source1, source2):
        pa, pb = self.prev(key + "a"), self.prev(key + "b")
        self.push(key + "a", source1)
        self.push(key + "b", source2)
        return (T(source1 > source2) and T(pa <= pb)) or (T(source1 < source2) and T(pa >= pb))

    def cum(self, key, source):
        p = self.prev(key, 0.0)
        v = p + nz(source)
        self.push(key, v)
        return v

    def macd(self, key, source, fastlen, slowlen, siglen):
        m = self.ema(key + "f", source, fastlen) - self.ema(key + "s", source, slowlen)
        s = self.ema(key + "g", m, siglen)
        return m, s, m - s

    def bb(self, key, series, length, mult):
        basis = self.sma(key + "m", series, length)
        dev = mult * self.stdev(key + "d", series, length)
        return basis, basis + dev, basis - dev

    def bbw(self, key, series, length, mult):
        basis, up, lo = self.bb(key, series, length, mult)
        return (up - lo) / basis if basis else NA

    def kc(self, key, series, length, mult, useTrueRange=True):
        basis = self.ema(key + "b", series, length)
        rng = self.tr(None, True) if T(useTrueRange) else self.high - self.low
        r = self.ema(key + "r", rng, length)
        return basis, basis + r * mult, basis - r * mult

    def stoch(self, key, source, high, low, length):
        hh = self.highest(key + "h", high, length)
        ll = self.lowest(key + "l", low, length)
        return 100 * (source - ll) / (hh - ll) if hh != ll and not na(hh) else NA

    def cci(self, key, source, length):
        w = self._window(key, source, length)
        if w is None:
            return NA
        m = sum(w) / len(w)
        d = sum(abs(x - m) for x in w) / len(w)
        return (source - m) / (0.015 * d) if d else NA

    def mfi(self, key, series, length):
        ch = self.change(key + "c", series)
        up = self.msum(key + "u", self.volume * (0.0 if na(ch) or ch <= 0 else series), length)
        dn = self.msum(key + "d", self.volume * (0.0 if na(ch) or ch >= 0 else series), length)
        if na(up) or na(dn):
            return NA
        return 100.0 if dn == 0 else 100.0 - 100.0 / (1.0 + up / dn)

    def wpr(self, key, length):
        hh = self.highest(key + "h", self.high, length)
        ll = self.lowest(key + "l", self.low, length)
        return 100 * (self.close - hh) / (hh - ll) if hh != ll and not na(hh) else NA

    def cmo(self, key, source, length):
        ch = self.change(key + "c", source)
        s1 = self.msum(key + "u", 0.0 if na(ch) or ch < 0 else ch, length)
        s2 = self.msum(key + "d", 0.0 if na(ch) or ch >= 0 else -ch, length)
        return 100 * (s1 - s2) / (s1 + s2) if (s1 + s2) else NA

    def supertrend(self, key, factor, atrPeriod):
        s = self.st(key)
        p = s["p"]
        a = self.atr(key + "a", atrPeriod)
        src = self.hl2
        up, lo = src + factor * a, src - factor * a
        pc = self.h("close", 1)
        if p is not None:
            plo, pup = nz(p["lo"]), nz(p["up"])
            lo = lo if (lo > plo or pc < plo) else plo
            up = up if (up < pup or pc > pup) else pup
        if p is None or na(p["atr"]):
            direction = 1
        elif p["st"] == p["up"]:
            direction = -1 if self.close > up else 1
        else:
            direction = 1 if self.close < lo else -1
        st = lo if direction == -1 else up
        s["c"] = {"lo": lo, "up": up, "atr": a, "st": st}
        return st, direction

    def dmi(self, key, diLength, adxSmoothing):
        up = self.change(key + "h", self.high)
        down = -self.change(key + "l", self.low)
        plus_dm = NA if na(up) else (up if up > down and up > 0 else 0.0)
        minus_dm = NA if na(down) else (down if down > up and down > 0 else 0.0)
        trur = self.rma(key + "t", self.tr(None, False), diLength)
        plus = self.fixnan(key + "p", 100 * self.rma(key + "pp", plus_dm, diLength) / trur if trur else NA)
        minus = self.fixnan(key + "m", 100 * self.rma(key + "mm", minus_dm, diLength) / trur if trur else NA)
        tot = plus + minus
        adx = 100 * self.rma(key + "a", abs(plus - minus) / (1 if tot == 0 else tot), adxSmoothing)
        return plus, minus, adx

    def sar(self, key, start, inc, max_):
        s = self.st(key)
        p = s["p"] or {"r": NA, "mm": NA, "acc": NA, "below": False}
        r, mm, acc, below = p["r"], p["mm"], p["acc"], p["below"]
        i = self.i
        hi, lo = self.high, self.low
        first = False
        if i == 1:
            if self.close > self.h("close", 1):
                below, mm, r = True, hi, self.h("low", 1)
            else:
                below, mm, r = False, lo, self.h("high", 1)
            first, acc = True, start
        r = r + acc * (mm - r)
        if below:
            if r > lo:
                first, below, r, mm, acc = True, False, max(hi, mm), lo, start
        else:
            if r < hi:
                first, below, r, mm, acc = True, True, min(lo, mm), hi, start
        if not first:
            if below and hi > mm:
                mm, acc = hi, min(acc + inc, max_)
            elif not below and lo < mm:
                mm, acc = lo, min(acc + inc, max_)
        if below:
            r = min(r, self.h("low", 1))
            if i > 1:
                r = min(r, self.h("low", 2))
        else:
            r = max(r, self.h("high", 1))
            if i > 1:
                r = max(r, self.h("high", 2))
        s["c"] = {"r": r, "mm": mm, "acc": acc, "below": below}
        return r

    def _pivot(self, key, args, default_src, high: bool):
        if len(args) == 2:
            src, left, right = default_src, args[0], args[1]
        else:
            src, left, right = args
        left, right = int(left), int(right)
        b = self.push(key, src)
        if len(b) - 1 < left + right + 1:
            return NA
        w = b[len(b) - (left + right + 1):]
        c = w[left]
        if na(c):
            return NA
        others_l, others_r = w[:left], w[left + 1:]
        if high:
            ok = all(c > x for x in others_l if not na(x)) and all(c >= x for x in others_r if not na(x))
        else:
            ok = all(c < x for x in others_l if not na(x)) and all(c <= x for x in others_r if not na(x))
        return c if ok else NA

    def pivothigh(self, key, *args):
        return self._pivot(key, args, self.high, True)

    def pivotlow(self, key, *args):
        return self._pivot(key, args, self.low, False)

    def valuewhen(self, key, condition, source, occurrence=0):
        s = self.state.setdefault(key, {"vals": [], "bar": -1, "added": False})
        if s["bar"] == self.i and s["added"]:
            s["vals"].pop()
        if s["bar"] != self.i:
            s["bar"] = self.i
        s["added"] = T(condition)
        if s["added"]:
            s["vals"].append(source)
        n = int(occurrence)
        return s["vals"][-1 - n] if n < len(s["vals"]) else NA

    def barssince(self, key, condition):
        s = self.st(key)
        last = s["p"]
        cur = self.i if T(condition) else last
        s["c"] = cur
        return NA if cur is None else self.i - cur

    def rising(self, key, source, length):
        b = self.push(key, source)
        n = int(length)
        if len(b) - 1 <= n:
            return False
        prev = b[len(b) - 1 - n:-1]
        return all(T(source > x) for x in prev)

    def falling(self, key, source, length):
        b = self.push(key, source)
        n = int(length)
        if len(b) - 1 <= n:
            return False
        prev = b[len(b) - 1 - n:-1]
        return all(T(source < x) for x in prev)

    def median(self, key, source, length):
        w = self._window(key, source, length)
        return NA if w is None else float(np.median(w))

    def linreg(self, key, source, length, offset=0):
        w = self._window(key, source, length)
        if w is None:
            return NA
        n = len(w)
        xs = range(n)
        mx, my = (n - 1) / 2, sum(w) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, w)) / sxx if sxx else 0.0
        return my - slope * mx + slope * (n - 1 - offset)

    def percentrank(self, key, source, length):
        b = self.push(key, source)
        n = int(length)
        if len(b) - 1 <= n:
            return NA
        prev = b[len(b) - 1 - n:-1]
        return 100.0 * sum(1 for x in prev if x <= source) / n

    def correlation(self, key, source1, source2, length):
        a = self._window(key + "a", source1, length)
        b = self._window(key + "b", source2, length)
        if a is None or b is None:
            return NA
        c = np.corrcoef(a, b)[0, 1]
        return float(c)

    def range_(self, key, source, length):
        return self.highest(key + "h", source, length) - self.lowest(key + "l", source, length)

    def alma(self, key, series, length, offset=0.85, sigma=6.0, floor=False):
        w = self._window(key, series, length)
        if w is None:
            return NA
        n = len(w)
        m = offset * (n - 1)
        if T(floor):
            m = math.floor(m)
        s = n / sigma
        wts = [math.exp(-((k - m) ** 2) / (2 * s * s)) for k in range(n)]
        return sum(x * wt for x, wt in zip(w, wts)) / sum(wts)

    def swma(self, key, source):
        w = self._window(key, source, 4)
        return NA if w is None else (w[0] + 2 * w[1] + 2 * w[2] + w[3]) / 6

    def obv(self, key):
        ch = self.change(key + "c", self.close)
        sign = 0.0 if na(ch) else (1.0 if ch > 0 else -1.0 if ch < 0 else 0.0)
        return self.cum(key, sign * self.volume)

    def vwap(self, key, source=None, anchor=None, stdev_mult=None):
        src = self.hlc3 if source is None else source
        s = self.st(key)
        p = s["p"]
        day = (self.year, self.month, self.dayofmonth)
        new = p is None or p["day"] != day or T(anchor)
        pv = (0.0 if new else p["pv"]) + src * self.volume
        vol = (0.0 if new else p["v"]) + self.volume
        s["c"] = {"day": day, "pv": pv, "v": vol}
        return pv / vol if vol else NA

    def allmax(self, key, source):
        p = self.prev(key)
        v = source if na(p) else (p if na(source) else max(p, source))
        self.push(key, v)
        return v

    def allmin(self, key, source):
        p = self.prev(key)
        v = source if na(p) else (p if na(source) else min(p, source))
        self.push(key, v)
        return v

    def msum(self, key, source, length):
        w = self._window(key, source, length)
        return NA if w is None else sum(w)

    def fixnan(self, key, source):
        p = self.prev(key)
        v = p if na(source) else source
        self.push(key, v)
        return v

    # ------------------------------------------------------- time & frames

    def _local(self, t):
        return pd.Timestamp(int(t), unit="ms", tz="UTC").tz_convert(self.tz)

    def tf_change(self, key, timeframe):
        ids = self._ids(timeframe)
        if ids is None:
            return False
        return self.i > 0 and ids[self.i] != ids[self.i - 1]

    def _ids(self, timeframe):
        k = "ids:" + str(timeframe)
        if k not in self._htf_cache:
            self._htf_cache[k] = _period_ids(self.index, str(timeframe), self.session_open)
        return self._htf_cache[k]

    def security(self, timeframe, field, n, lookahead_on):
        """request.security() for a built-in price series on a higher timeframe."""
        tf = str(timeframe or "")
        if not tf or tf_seconds(tf) <= self.bar_minutes * 60:
            return self.h(field, n)
        k = f"htf:{tf}"
        if k not in self._htf_cache:
            ids = self._ids(tf)
            if ids is None:
                return self.h(field, n)
            a = self.arrays
            df = pd.DataFrame({"id": ids, "open": a["open"], "high": a["high"], "low": a["low"],
                               "close": a["close"], "volume": a["volume"]})
            g = df.groupby("id", sort=False)
            agg = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                                "close": g["close"].last(), "volume": g["volume"].sum()})
            agg["hl2"] = (agg.high + agg.low) / 2
            agg["hlc3"] = (agg.high + agg.low + agg.close) / 3
            agg["ohlc4"] = (agg.open + agg.high + agg.low + agg.close) / 4
            agg["hlcc4"] = (agg.high + agg.low + 2 * agg.close) / 4
            pos = pd.Series(np.arange(len(agg)), index=agg.index)
            idx = pos.loc[ids].to_numpy()
            last = np.append(idx[1:] != idx[:-1], True)
            self._htf_cache[k] = (idx.tolist(), last.tolist(), {c: agg[c].tolist() for c in agg.columns})
        idx, last, fields = self._htf_cache[k]
        if field not in fields:
            return self.h(field, n)
        cur = idx[self.i]
        base = cur if (lookahead_on or last[self.i]) else cur - 1
        j = base - int(n)
        return fields[field][j] if j >= 0 else NA

    def time_fn(self, timeframe=None, session=None, timezone=None, *_, **__):
        t = self.time
        if session is None or na(session) or session == "":
            return t
        sess = str(session)
        if sess.lower() in ("24x7", "0000-0000"):
            return t
        days = None
        if ":" in sess:
            sess, d = sess.split(":", 1)
            days = {int(ch) for ch in d if ch.isdigit()}
        lt = self._local(t) if timezone in (None, "") else pd.Timestamp(int(t), unit="ms", tz="UTC").tz_convert(
            _tz_name(timezone))
        dow = (lt.dayofweek + 1) % 7 + 1
        hm = lt.hour * 100 + lt.minute
        ok = False
        for part in sess.split(","):
            if "-" not in part:
                continue
            a, b = part.split("-")[:2]
            a, b = int(a[:4]), int(b[:4])
            ok = (a <= hm < b) if a < b else (hm >= a or hm < b)
            if ok:
                break
        if ok and days is not None and dow not in days:
            ok = False
        return t if ok else NA

    def timestamp(self, *args):
        tz = self.tz
        if args and isinstance(args[0], str) and len(args) > 1:
            tz, args = _tz_name(args[0]), args[1:]
        if len(args) == 1 and isinstance(args[0], str):
            ts = pd.Timestamp(args[0])
            ts = ts.tz_localize(tz) if ts.tzinfo is None else ts
            return int(ts.value // 1_000_000)
        parts = [int(x) for x in args] + [0] * (6 - len(args))
        y, mo, d, hh, mm, ss = parts[:6]
        ts = pd.Timestamp(datetime(y, max(1, mo), max(1, d), hh, mm, ss)).tz_localize(tz)
        return int(ts.value // 1_000_000)

    def date_part(self, part):
        def fn(t=None, timezone=None):
            if t is None:
                return getattr(self, part)
            if na(t):
                return NA
            lt = self._local(t) if not timezone else pd.Timestamp(int(t), unit="ms", tz="UTC").tz_convert(
                _tz_name(timezone))
            if part == "dayofweek":
                return (lt.dayofweek + 1) % 7 + 1
            if part == "dayofmonth":
                return lt.day
            if part == "weekofyear":
                return lt.isocalendar()[1]
            return getattr(lt, part)
        return fn

    # ----------------------------------------------------------- strategy

    def order_fn(self, name: str) -> Callable:
        params = (ORDER_PARAMS_V4 if self.version <= 4 else ORDER_PARAMS)[name]
        b = self.broker

        def call(*args, **kwargs):
            if len(args) > len(params):
                raise PineError(f"{name}() got too many positional arguments")
            kw = dict(zip(params, args))
            kw.update(kwargs)
            if "when" in kw and not T(kw["when"]):
                return NA
            kw.pop("when", None)
            if "long" in kw:
                kw["direction"] = "long" if T(kw.pop("long")) else "short"
            if name in ("strategy.entry", "strategy.order"):
                if "id" not in kw or "direction" not in kw:
                    raise PineError(f"{name}() needs an id and a direction")
                b.entry(kw["id"], kw["direction"], qty=kw.get("qty"), limit=kw.get("limit"),
                        stop=kw.get("stop"), comment=kw.get("comment") or "")
            elif name == "strategy.exit":
                b.exit(kw.get("id", "Exit"), from_entry=kw.get("from_entry", ""), qty=kw.get("qty"),
                       qty_percent=kw.get("qty_percent"), profit=kw.get("profit"), limit=kw.get("limit"),
                       loss=kw.get("loss"), stop=kw.get("stop"), trail_price=kw.get("trail_price"),
                       trail_points=kw.get("trail_points"), trail_offset=kw.get("trail_offset"))
            elif name == "strategy.close":
                b.close_position(kw.get("id"), immediately=T(kw.get("immediately", False)),
                                 qty=kw.get("qty"), qty_percent=kw.get("qty_percent"))
            elif name == "strategy.close_all":
                b.close_all(immediately=T(kw.get("immediately", False)))
            elif name == "strategy.cancel":
                b.cancel(kw.get("id"))
            elif name == "strategy.cancel_all":
                b.cancel_all()
            return NA

        return call

    def allow_entry_in(self, value=None, *_, **__):
        v = str(value or "all").lower()
        self.broker.cfg.allow_long = v in ("all", "long")
        self.broker.cfg.allow_short = v in ("all", "short")
        return NA

    def closed(self, attr):
        def fn(trade_num):
            j = int(trade_num)
            if not 0 <= j < len(self.broker.trades):
                return NA
            t = self.broker.trades[j]
            return {"profit": t.pnl, "entry_price": t.entry_price, "exit_price": t.exit_price,
                    "entry_bar_index": t.entry_bar, "exit_bar_index": t.exit_bar, "entry_time": t.entry_time,
                    "exit_time": t.exit_time, "size": t.direction * t.qty, "commission": t.commission,
                    "entry_id": t.entry_id, "exit_id": t.exit_id, "profit_percent": t.pnl_pct}.get(attr, NA)
        return fn

    def opened(self, attr):
        def fn(trade_num):
            j = int(trade_num)
            if not 0 <= j < len(self.broker.lots):
                return NA
            l = self.broker.lots[j]
            return {"entry_price": l.price, "entry_bar_index": l.bar, "entry_time": l.time,
                    "size": l.direction * l.qty, "entry_id": l.entry_id, "commission": l.commission,
                    "profit": l.direction * l.qty * (self.close - l.price) - l.commission}.get(attr, NA)
        return fn


def _tz_name(tz: str) -> str:
    tz = str(tz)
    if tz.upper().startswith(("GMT", "UTC")) and len(tz) > 3:
        sign = tz[3]
        hours = tz[4:].split(":")[0]
        flipped = "-" if sign == "+" else "+"
        return f"Etc/GMT{flipped}{int(hours)}"          # POSIX sign convention is inverted
    return tz


# ------------------------------------------------------------------ stateless built-ins

def _div(a, b):
    try:
        return a / b
    except ZeroDivisionError:
        return NA
    except TypeError:
        return NA


def _mod(a, b):
    try:
        return math.fmod(a, b)
    except (ZeroDivisionError, ValueError, TypeError):
        return NA


def _nums(args):
    vals = [a for a in args]
    if any(na(a) for a in vals):
        return None
    return vals


def _max(*args):
    v = _nums(args)
    return NA if v is None else max(v)


def _min(*args):
    v = _nums(args)
    return NA if v is None else min(v)


def _avg(*args):
    v = _nums(args)
    return NA if v is None else sum(v) / len(v)


def _round(x, precision=None):
    if na(x):
        return NA
    if precision is None or na(precision):
        return int(math.floor(x + 0.5))
    return round(x, int(precision))


def _wrap1(fn):
    def call(x, *a):
        if na(x):
            return NA
        try:
            return fn(x, *a)
        except (ValueError, OverflowError, ZeroDivisionError):
            return NA
    return call


def _int(x):
    return NA if na(x) else int(x)


def _tostring(value, format=None):
    if na(value):
        return "NaN"
    if isinstance(value, float) and value.is_integer() and format is None:
        return str(value)
    return str(round(value, 8) if isinstance(value, float) else value)


def _iff(condition, then, _else):
    return then if T(condition) else _else


STATELESS: dict[str, Callable] = {
    "na": na, "nz": nz, "iff": _iff, "int": _int, "float": lambda x: _f(x), "bool": lambda x: T(x),
    "math.abs": _wrap1(abs), "math.max": _max, "math.min": _min, "math.avg": _avg, "math.round": _round,
    "math.floor": _wrap1(lambda x: int(math.floor(x))), "math.ceil": _wrap1(lambda x: int(math.ceil(x))),
    "math.sqrt": _wrap1(math.sqrt), "math.pow": _wrap1(lambda x, y: math.pow(x, y)), "math.exp": _wrap1(math.exp),
    "math.log": _wrap1(math.log), "math.log10": _wrap1(math.log10),
    "math.sign": _wrap1(lambda x: 1.0 if x > 0 else -1.0 if x < 0 else 0.0),
    "math.sin": _wrap1(math.sin), "math.cos": _wrap1(math.cos), "math.tan": _wrap1(math.tan),
    "math.asin": _wrap1(math.asin), "math.acos": _wrap1(math.acos), "math.atan": _wrap1(math.atan),
    "math.todegrees": _wrap1(math.degrees), "math.toradians": _wrap1(math.radians),
    "math.random": lambda min=0.0, max=1.0, seed=None: _random.uniform(min, max),
    "str.tostring": _tostring, "str.format": lambda fmt, *a, **k: str(fmt),
    "str.tonumber": lambda s: _f(s), "str.length": lambda s: len(str(s)),
    "str.contains": lambda source, str_: str(str_) in str(source),
    "str.startswith": lambda source, str_: str(source).startswith(str(str_)),
    "str.endswith": lambda source, str_: str(source).endswith(str(str_)),
    "str.upper": lambda s: str(s).upper(), "str.lower": lambda s: str(s).lower(),
    "str.replace_all": lambda source, target, replacement: str(source).replace(str(target), str(replacement)),
    "timeframe.in_seconds": lambda timeframe="": tf_seconds(timeframe),
}

# Stateful built-ins: name -> Runtime method name. They receive the call-site key first.
STATEFUL: dict[str, str] = {
    "ta.sma": "sma", "ta.ema": "ema", "ta.rma": "rma", "ta.wma": "wma", "ta.vwma": "vwma", "ta.hma": "hma",
    "ta.rsi": "rsi", "ta.atr": "atr", "ta.tr": "tr", "ta.macd": "macd", "ta.bb": "bb", "ta.bbw": "bbw",
    "ta.kc": "kc", "ta.stdev": "stdev", "ta.variance": "variance", "ta.dev": "dev",
    "ta.highest": "highest", "ta.lowest": "lowest", "ta.highestbars": "highestbars", "ta.lowestbars": "lowestbars",
    "ta.crossover": "crossover", "ta.crossunder": "crossunder", "ta.cross": "cross", "ta.change": "change",
    "ta.mom": "change", "ta.cum": "cum", "ta.stoch": "stoch", "ta.cci": "cci", "ta.mfi": "mfi", "ta.wpr": "wpr",
    "ta.cmo": "cmo", "ta.supertrend": "supertrend", "ta.dmi": "dmi", "ta.sar": "sar",
    "ta.pivothigh": "pivothigh", "ta.pivotlow": "pivotlow", "ta.valuewhen": "valuewhen",
    "ta.barssince": "barssince", "ta.rising": "rising", "ta.falling": "falling", "ta.median": "median",
    "ta.linreg": "linreg", "ta.percentrank": "percentrank", "ta.correlation": "correlation",
    "ta.range": "range_", "ta.alma": "alma", "ta.swma": "swma", "ta.vwap": "vwap", "ta.max": "allmax",
    "ta.min": "allmin", "math.sum": "msum", "fixnan": "fixnan", "timeframe.change": "tf_change",
    "ta.roc": "roc",
}

# Built-in variables that are really stateful calls: `ta.tr`, `ta.vwap`, `ta.obv`.
STATEFUL_VARS = {"ta.tr": ("tr", "False"), "ta.vwap": ("vwap", ""), "ta.obv": ("obv", "")}


def _roc(self, key, source, length):
    b = self.push(key, source)
    n = int(length)
    if len(b) - 1 <= n:
        return NA
    p = b[-1 - n]
    return 100 * (source - p) / p if p else NA


Runtime.roc = _roc
