"""Pine Script: indicator accuracy, language semantics and error reporting."""
import numpy as np
import pandas as pd
import pytest

from backtester.pine.compiler import compile_pine
from backtester.pine.runtime import PineError

from conftest import make_candles, run_pine

HEADER = '//@version=5\nstrategy("t")\n'
ORDER = '\nif bar_index == 1000000\n    strategy.entry("L", strategy.long)\n'


def values(body: str, candles):
    _, v = run_pine(HEADER + body + ORDER, candles)
    return {k: np.array([np.nan if x is None else float(x) for x in vals]) for k, vals in v.items()
            if not any(isinstance(x, str) for x in vals)}


def close_enough(a, b, skip=0):
    a, b = np.asarray(a, float)[skip:], np.asarray(b, float)[skip:]
    assert a.shape == b.shape
    assert np.array_equal(np.isnan(a), np.isnan(b)), "na positions differ"
    m = ~np.isnan(a)
    np.testing.assert_allclose(a[m], b[m], rtol=1e-9, atol=1e-9)


def pine_ema(x: pd.Series, n: int, alpha: float) -> np.ndarray:
    """Pine's ema/rma: seeded with the SMA of the first n values."""
    out = np.full(len(x), np.nan)
    for i in range(len(x)):
        if i < n - 1:
            continue
        prev = out[i - 1] if i > 0 else np.nan
        out[i] = x.iloc[i - n + 1:i + 1].mean(skipna=False) if np.isnan(prev) else alpha * x.iloc[i] + (1 - alpha) * prev
    return out


def test_moving_averages_match_reference(candles):
    v = values("a = ta.sma(close, 20)\nb = ta.ema(close, 9)\nc = ta.rma(close, 14)\nd = ta.wma(close, 5)",
               candles)
    c = candles["close"]
    close_enough(v["a"], c.rolling(20).mean())
    close_enough(v["b"], pine_ema(c, 9, 2 / 10))
    close_enough(v["c"], pine_ema(c, 14, 1 / 14))
    w = np.arange(1, 6)
    close_enough(v["d"], c.rolling(5).apply(lambda x: (x * w).sum() / w.sum(), raw=True))


def test_rsi_atr_stdev_highest_match_reference(candles):
    v = values("r = ta.rsi(close, 14)\na = ta.atr(14)\ns = ta.stdev(close, 20)\n"
               "h = ta.highest(high, 10)\nl = ta.lowest(10)", candles)
    c, h, l = candles["close"], candles["high"], candles["low"]
    d = c.diff()
    up = pine_ema(d.clip(lower=0), 14, 1 / 14)
    dn = pine_ema((-d).clip(lower=0), 14, 1 / 14)
    close_enough(v["r"], 100 - 100 / (1 + up / dn))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    close_enough(v["a"], pine_ema(tr, 14, 1 / 14))
    close_enough(v["s"], c.rolling(20).std(ddof=0))
    close_enough(v["h"], h.rolling(10).max())
    close_enough(v["l"], l.rolling(10).min())


def test_macd_and_bollinger(candles):
    v = values("[m, s, hh] = ta.macd(close, 12, 26, 9)\n[b, u, lo] = ta.bb(close, 20, 2)", candles)
    c = candles["close"]
    m = pine_ema(c, 12, 2 / 13) - pine_ema(c, 26, 2 / 27)
    close_enough(v["m"], m)
    close_enough(v["hh"], v["m"] - v["s"])
    close_enough(v["u"] - v["b"], 2 * c.rolling(20).std(ddof=0))


def test_crossover_and_history(candles):
    v = values("f = ta.sma(close, 5)\ns = ta.sma(close, 20)\nx = ta.crossover(f, s) ? 1 : 0\n"
               "p = close[3]\nq = f[1]", candles)
    f, s = candles["close"].rolling(5).mean(), candles["close"].rolling(20).mean()
    ref = ((f > s) & (f.shift() <= s.shift())).astype(float)
    close_enough(v["x"], ref)
    close_enough(v["p"], candles["close"].shift(3))
    close_enough(v["q"], f.shift(1))


def test_var_persists_and_plain_variables_reset(candles):
    v = values("var count = 0\ncount := count + 1\nplain = 0\nplain := plain + 1", candles)
    assert v["count"][-1] == len(candles)
    assert set(v["plain"]) == {1.0}


def test_if_and_switch_expressions():
    c = make_candles(50)
    body = """
x = if close > open
    1
else if close < open
    -1
else
    0
mode = "b"
y = switch mode
    "a" => 10
    "b" => 20
    => 30
z = switch
    close > open => 1
    => 2
"""
    v = values(body, c)
    ref = np.sign(c["close"] - c["open"])
    close_enough(v["x"], ref)
    assert set(v["y"]) == {20.0}
    close_enough(v["z"], np.where(c["close"] > c["open"], 1.0, 2.0))


def test_user_functions_keep_separate_state_per_call(candles):
    body = """
smooth(src, len) =>
    ta.ema(src, len)
a = smooth(close, 5)
b = smooth(close, 30)
calc(x) =>
    y = x * 2
    y + 1
c = calc(3)
"""
    v = values(body, candles)
    close_enough(v["a"], pine_ema(candles["close"], 5, 2 / 6))
    close_enough(v["b"], pine_ema(candles["close"], 30, 2 / 31))
    assert set(v["c"]) == {7.0}


def test_loops_and_tuples():
    body = """
total = 0.0
for i = 1 to 4
    total += i
n = 0
while n < 3
    n += 1
f() =>
    [1, 2]
[p, q] = f()
"""
    v = values(body, make_candles(10))
    assert set(v["total"]) == {10.0}
    assert set(v["n"]) == {3.0}
    assert set(v["p"]) == {1.0} and set(v["q"]) == {2.0}


def test_supertrend_flips_direction(candles):
    v = values("[st, dir] = ta.supertrend(3, 10)", candles)
    d = v["dir"][20:]
    assert set(np.unique(d)) <= {-1.0, 1.0} and len(set(d)) == 2
    st, c = v["st"][20:], candles["close"].to_numpy()[20:]
    assert np.all((d == -1) <= (st <= c + 1e-9))       # in an uptrend the line sits below price


def test_v4_syntax_runs():
    src = """//@version=4
strategy("v4", overlay=true)
len = input(14, title="Length", type=input.integer)
r = rsi(close, len)
if crossover(r, 50)
    strategy.entry("L", strategy.long, when = r > 0)
strategy.close("L", when = crossunder(r, 50))
"""
    b, _ = run_pine(src, make_candles(400))
    assert len(b.trades) > 3


def test_request_security_higher_timeframe():
    c = make_candles(24 * 10)
    v = values('d = request.security(syminfo.tickerid, "D", close[1], lookahead=barmerge.lookahead_on)\n'
               'e = request.security(syminfo.tickerid, "D", close)', c)
    daily = c["close"].groupby(c.index.date).last()
    prev_day = c.index.date
    days = sorted(set(prev_day))
    exp = [daily[days[days.index(d) - 1]] if days.index(d) > 0 else np.nan for d in prev_day]
    close_enough(v["d"], exp)
    # lookahead off: the last completed day, updating on the day's final bar
    last_bar = np.r_[c.index.date[1:] != c.index.date[:-1], True]
    exp_e = [daily[d] if lb else (daily[days[days.index(d) - 1]] if days.index(d) > 0 else np.nan)
             for d, lb in zip(c.index.date, last_bar)]
    close_enough(v["e"], exp_e)


def test_strategy_settings_are_read():
    src = """//@version=5
strategy("My strat", initial_capital=5000, default_qty_type=strategy.percent_of_equity, default_qty_value=50,
     commission_type=strategy.commission.percent, commission_value=0.1, pyramiding=3)
if close > open
    strategy.entry("L", strategy.long)
"""
    d = compile_pine(src).declaration
    assert (d.title, d.initial_capital, d.default_qty_type, d.default_qty_value) == ("My strat", 5000, "percent_of_equity", 50)
    assert (d.commission_type, d.commission_value, d.pyramiding) == ("percent", 0.1, 3)


@pytest.mark.parametrize("src, needle, line", [
    ('//@version=5\nindicator("x")\nplot(close)', "indicator, not a strategy", None),
    ('//@version=5\nstrategy("x")\nplot(close)', "never places an order", None),
    ('//@version=5\nstrategy("x")\na = ta.foo(close)\nstrategy.entry("L", strategy.long)', "ta.foo", 3),
    ('//@version=5\nstrategy("x")\nb = undefined_thing + 1\nstrategy.entry("L", strategy.long)', "undefined_thing", 3),
    ('//@version=5\nstrategy("x")\na = array.new_float(3)\nstrategy.entry("L", strategy.long)', "arrays", 3),
    ('//@version=5\nstrategy("x")\nif close >\nstrategy.entry("L", strategy.long)', "", None),
])
def test_compile_errors_are_clear(src, needle, line):
    with pytest.raises(PineError) as exc:
        compile_pine(src)
    assert needle in exc.value.message
    if line:
        assert exc.value.line == line


def test_runtime_error_points_at_script_line():
    src = '//@version=5\nstrategy("x")\nx = "a"\ny = x - 1\nstrategy.entry("L", strategy.long)\n'
    compiled = compile_pine(src)
    with pytest.raises(TypeError) as exc:
        run_pine(src, make_candles(5))
    assert compiled.pine_line(exc.value) == 4


def test_script_strings_stay_data():
    # a hostile string is passed through as a plain value, never executed
    payload = "__import__('os').system('echo hacked')"
    src = f'//@version=5\nstrategy("x")\ns = "{payload}"\nif bar_index == 2\n    strategy.entry(s, strategy.long)\n'
    b, v = run_pine(src, make_candles(10))
    assert v["s"][0] == payload
    assert b.trades[0].entry_id == payload
