"""RSI mean reversion: buy when RSI(14) dips below 30, sell when it rises above 70.

A Python strategy defines generate_signals(data). `data` is a DataFrame indexed by
bar time with columns open, high, low, close, volume. Return the position you want
after each bar: 1 = long, -1 = short, 0 = flat, NaN = keep the current position.
Orders fill at the next bar's open.
"""
import numpy as np
import pandas as pd

NAME = "RSI mean reversion"


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def generate_signals(data: pd.DataFrame) -> pd.Series:
    r = rsi(data["close"])
    position = pd.Series(np.nan, index=data.index)
    position[r < 30] = 1      # oversold: go long
    position[r > 70] = 0      # overbought: exit
    return position
