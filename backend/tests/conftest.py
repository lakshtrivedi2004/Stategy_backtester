import numpy as np
import pandas as pd
import pytest

from backtester.engine.broker import Broker, BrokerConfig
from backtester.pine.compiler import compile_pine
from backtester.pine.runtime import Runtime
from backtester.runner import simulate


def make_candles(n: int = 600, seed: int = 3, freq: str = "h") -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-06", periods=n, freq=freq, tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    vol = rng.uniform(100, 1000, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


def frame(rows, start="2025-01-06", freq="h") -> pd.DataFrame:
    """Candles from (open, high, low, close) tuples."""
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1.0}, index=idx)


def broker(**kw) -> Broker:
    cfg = dict(initial_capital=10_000.0, commission=lambda n, q, s: 0.0, slippage=0.0, mintick=0.01,
               fractional=True, qty_type="fixed", qty_value=1.0)
    cfg.update(kw)
    return Broker(BrokerConfig(**cfg))


def run_pine(source: str, candles: pd.DataFrame, start: int = 0, **broker_kw):
    """Run a Pine script; returns (broker, {variable name: values per bar})."""
    compiled = compile_pine(source)
    b = broker(**broker_kw)
    rt = Runtime(candles, b, symbol="TEST", mintick=0.01, sym_type="crypto", currency="USD", timezone="UTC",
                 bar_minutes=60, version=compiled.version)
    bar = compiled.bind(rt)

    def on_bar(i):
        rt.begin(i)
        bar()
        rt.commit()

    simulate(candles, start, b, on_bar)
    values = {name: v.hist for name, v in zip(compiled.var_names, rt.vars)}
    return b, values


@pytest.fixture
def candles():
    return make_candles()
