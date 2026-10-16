"""Donchian breakout with a stop-loss and a 2R take-profit.

Return a DataFrame with a `signal` column (1 = buy, -1 = sell short, 0 = nothing)
and optional `stop_loss` / `take_profit` prices for the new position.
"""
import numpy as np
import pandas as pd

NAME = "Donchian breakout (2R target)"


def generate_signals(data: pd.DataFrame) -> pd.DataFrame:
    upper = data["high"].rolling(20).max().shift(1)
    lower = data["low"].rolling(20).min().shift(1)

    out = pd.DataFrame(index=data.index)
    out["signal"] = 0
    out.loc[data["close"] > upper, "signal"] = 1
    out.loc[data["close"] < lower, "signal"] = -1

    risk = (upper - lower) / 2
    out["stop_loss"] = np.where(out["signal"] == 1, data["close"] - risk, data["close"] + risk)
    out["take_profit"] = np.where(out["signal"] == 1, data["close"] + 2 * risk, data["close"] - 2 * risk)
    return out
