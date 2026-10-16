"""Historical candles from Yahoo Finance.

`load_history()` returns the candles for one instrument plus the index of the
first bar inside the tested window. Bars before that index are warm-up history,
so indicators such as a 200-bar average already have values when testing starts.
Downloads are kept in memory for a few minutes; nothing is written to disk.
"""
from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from .markets import Instrument, Market, Period

WARMUP_BARS = 500
_CACHE_TTL = 600
_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
_lock = threading.Lock()


class DataError(RuntimeError):
    pass


@dataclass
class History:
    candles: pd.DataFrame       # index: tz-aware datetime; columns open, high, low, close, volume
    start_index: int            # first bar of the tested window


def epoch_ms(index: pd.DatetimeIndex):
    """Unix time in milliseconds (independent of the index's storage resolution)."""
    return index.as_unit("ms").asi8


def _download(symbol: str, interval: str, fetch_days: int) -> pd.DataFrame:
    key = (symbol, interval, fetch_days)
    with _lock:
        hit = _cache.get(key)
        if hit and _time.time() - hit[0] < _CACHE_TTL:
            return hit[1]

    import yfinance as yf

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=fetch_days + 1)
    try:
        raw = yf.Ticker(symbol).history(start=start, end=end, interval=interval,
                                        auto_adjust=True, actions=False)
    except Exception as exc:  # network errors, Yahoo rate limits
        raise DataError(f"Could not download {symbol} from Yahoo Finance: {exc}") from exc
    if raw is None or raw.empty:
        raise DataError(f"Yahoo Finance returned no {interval} data for {symbol}.")
    with _lock:
        _cache[key] = (_time.time(), raw)
    return raw


def normalize(raw: pd.DataFrame, market: Market) -> pd.DataFrame:
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].copy()
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    df.index = df.index.tz_convert(market.timezone)
    df.index.name = "time"
    df = df.apply(pd.to_numeric, errors="coerce")
    df["volume"] = df["volume"].fillna(0.0)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df[["open", "high", "low", "close"]] > 0).all(axis=1)]
    df["high"] = df[["open", "high", "low", "close"]].max(axis=1)
    df["low"] = df[["open", "high", "low", "close"]].min(axis=1)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if market.session is not None and len(df) > 1 and (df.index[1:] - df.index[:-1]).min() < pd.Timedelta(days=1):
        t = df.index.time
        df = df[(t >= market.session[0]) & (t < market.session[1])]
    return df.astype(float)


def window_start(candles: pd.DataFrame, period: Period, market: Market) -> int:
    """Index of the first bar in the tested window, counted back from the latest bar."""
    last = candles.index[-1]
    if period.days == 1 and market.session is not None:
        start = last.normalize()                       # the last full trading session
    else:
        start = last - pd.Timedelta(days=period.days)
    return int(candles.index.searchsorted(start, side="left" if period.days == 1 else "right"))


def load_history(instrument: Instrument, market: Market, period: Period) -> History:
    raw = _download(instrument.symbol, period.interval, period.fetch_days)
    df = normalize(raw, market)
    if len(df) < 2:
        raise DataError(f"Not enough {period.interval} data for {instrument.symbol}.")
    start = window_start(df, period, market)
    if start >= len(df) - 1:
        raise DataError(f"No {period.interval} bars for {instrument.symbol} in the last {period.label.lower()}.")
    cut = max(0, start - WARMUP_BARS)
    return History(candles=df.iloc[cut:], start_index=start - cut)
