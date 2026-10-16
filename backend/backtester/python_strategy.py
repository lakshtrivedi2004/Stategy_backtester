"""Run a Python strategy file.

The file defines `generate_signals(data)` (or `generate_signals(data, params)`).
`data` is a DataFrame indexed by bar time with columns open, high, low, close, volume.
It returns either:

* a Series (or a DataFrame column `position`) holding the position wanted after
  each bar's close: 1 = long, -1 = short, 0 = flat, NaN = no change; or
* a DataFrame with a `signal` column (1 = enter long, -1 = enter short, 0 = nothing)
  and optionally `exit` / `exit_long` / `exit_short` (True closes the position)
  and `stop_loss` / `take_profit` (absolute prices for the new position).

Signals act on the close of their bar and fill at the next bar's open.
"""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine.broker import Broker
from .strategy import StrategyError

_ALIASES = {"exit": ("exit",), "exit_long": ("exit_long",), "exit_short": ("exit_short",),
            "stop": ("stop_loss", "stop", "sl"), "target": ("take_profit", "target", "tp")}


@dataclass
class Signals:
    mode: str                     # "position" | "signal"
    position: np.ndarray | None = None
    signal: np.ndarray | None = None
    exit: np.ndarray | None = None
    exit_long: np.ndarray | None = None
    exit_short: np.ndarray | None = None
    stop: np.ndarray | None = None
    target: np.ndarray | None = None


def load_function(source: str, filename: str, entry: str):
    namespace: dict = {"__name__": "user_strategy", "__file__": filename}
    exec(compile(source, filename, "exec"), namespace)
    fn = namespace.get(entry)
    if not callable(fn):
        raise StrategyError(f"`{entry}` is not a function")
    return fn


def call(fn, data: pd.DataFrame):
    try:
        n_params = len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        n_params = 1
    frame = data.copy()
    try:
        return fn(frame, {}) if n_params >= 2 else fn(frame)
    except KeyError as exc:
        raise StrategyError(f"KeyError {exc} inside the strategy. The data columns are: open, high, low, "
                            "close, volume (lowercase), indexed by bar time.") from None


def _col(out: pd.DataFrame, names, index, kind=float):
    for n in names:
        if n in out.columns:
            s = out[n].reindex(index)
            if kind is bool:
                return s.fillna(False).astype(bool).to_numpy()
            return pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
    return None


def normalize(out, index: pd.DatetimeIndex) -> Signals:
    if isinstance(out, (list, tuple, np.ndarray)):
        arr = np.asarray(out, dtype=float)
        if arr.shape != (len(index),):
            raise StrategyError(f"generate_signals returned {arr.shape[0] if arr.ndim else 0} values for "
                                f"{len(index)} bars")
        out = pd.Series(arr, index=index)
    if isinstance(out, pd.Series):
        if len(out) == len(index) and not out.index.equals(index):
            out = pd.Series(out.to_numpy(), index=index)
        return Signals("position", position=pd.to_numeric(out.reindex(index), errors="coerce").to_numpy(float))
    if not isinstance(out, pd.DataFrame):
        raise StrategyError("generate_signals must return a pandas Series or DataFrame, "
                            f"not {type(out).__name__}")
    if len(out) == len(index) and not out.index.equals(index):
        out = out.set_axis(index)
    if "position" in out.columns:
        return Signals("position", position=_col(out, ("position",), index))
    if "signal" not in out.columns:
        raise StrategyError("The returned DataFrame needs a `signal` or `position` column")
    return Signals("signal", signal=np.nan_to_num(_col(out, ("signal",), index)),
                   exit=_col(out, _ALIASES["exit"], index, bool),
                   exit_long=_col(out, _ALIASES["exit_long"], index, bool),
                   exit_short=_col(out, _ALIASES["exit_short"], index, bool),
                   stop=_col(out, _ALIASES["stop"], index), target=_col(out, _ALIASES["target"], index))


def _main(s: Signals) -> np.ndarray:
    return s.position if s.mode == "position" else s.signal


def lookahead_suspected(fn, data: pd.DataFrame, full: Signals, start: int) -> bool:
    """Re-run the strategy on truncated data. Causal code gives identical signals for
    every bar it saw both times; code that peeks at future bars does not."""
    n = len(data)
    for frac in (0.5, 0.8):
        k = start + int((n - start) * frac)
        if k < 50 or k >= n - 1:
            continue
        part = normalize(call(fn, data.iloc[:k]), data.index[:k])
        a, b = _main(part)[:k], _main(full)[:k]
        same = np.isclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True)
        if not same.all():
            return True
    return False


def driver(s: Signals, broker: Broker):
    def _num(arr, i):
        if arr is None:
            return None
        v = arr[i]
        return None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)

    def on_bar(i: int) -> None:
        if not broker.active:
            return
        size = broker.position_size
        cur = 1 if size > 0 else -1 if size < 0 else 0
        if s.mode == "position":
            want = s.position[i]
            if math.isnan(want):
                return
            if want > 0 and cur <= 0:
                broker.entry("Long", "long")
            elif want < 0 and cur >= 0:
                broker.entry("Short", "short")
            elif want == 0 and cur != 0:
                broker.close_all()
            return
        closing = False
        if cur != 0 and s.exit is not None and s.exit[i]:
            closing = True
        if cur > 0 and s.exit_long is not None and s.exit_long[i]:
            closing = True
        if cur < 0 and s.exit_short is not None and s.exit_short[i]:
            closing = True
        if closing:
            broker.close_all()
        sig = s.signal[i]
        if sig > 0 and (cur <= 0 or closing):
            broker.entry("Long", "long")
            stop, target = _num(s.stop, i), _num(s.target, i)
            if stop is not None or target is not None:
                broker.exit("Long exit", "Long", stop=stop, limit=target)
        elif sig < 0 and (cur >= 0 or closing):
            broker.entry("Short", "short")
            stop, target = _num(s.stop, i), _num(s.target, i)
            if stop is not None or target is not None:
                broker.exit("Short exit", "Short", stop=stop, limit=target)

    return on_bar
