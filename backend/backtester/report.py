"""Turn a finished simulation into the report sent to the browser."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .data import epoch_ms
from .engine.broker import Broker, LONG
from .markets import Market

MAX_CURVE_POINTS = 1500
MAX_TRADES = 2000


def _r(x, nd: int = 2):
    """JSON-safe rounding: NaN / inf -> None."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def _streak(flags) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def _fmt_time(ms: int, tz: str, intraday: bool) -> str:
    ts = pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert(tz)
    return ts.strftime("%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d")


def _side_stats(trades) -> dict:
    pnl = np.array([t.pnl for t in trades], dtype=float)
    return {
        "trades": len(trades),
        "win_rate": _r(100 * (pnl > 0).mean()) if len(pnl) else None,
        "net_profit": _r(pnl.sum()) if len(pnl) else 0.0,
        "avg_trade": _r(pnl.mean()) if len(pnl) else None,
    }


def build(broker: Broker, candles: pd.DataFrame, start: int, market: Market, intraday: bool) -> dict:
    cfg = broker.cfg
    initial = cfg.initial_capital
    window = candles.iloc[start:]
    times = window.index
    eq = np.asarray(broker.equity_curve, dtype=float)
    n = min(len(eq), len(window))
    eq, times, window = eq[:n], times[:n], window.iloc[:n]
    final = float(eq[-1]) if n else initial

    bh = initial * window["close"].to_numpy() / float(window["open"].iloc[0]) if n else np.array([])
    peak = np.maximum.accumulate(eq) if n else np.array([])
    dd_pct = (eq / peak - 1) * 100 if n else np.array([])
    max_dd_abs = float((peak - eq).max()) if n else 0.0
    max_dd_pct = float(-dd_pct.min()) if n else 0.0

    trades = broker.trades
    pnl = np.array([t.pnl for t in trades], dtype=float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
    span_days = (times[-1] - times[0]).total_seconds() / 86400 if n > 1 else 0.0

    # daily-return ratios (only meaningful with enough days)
    eq_s = pd.Series(eq, index=times)
    daily = eq_s.groupby(eq_s.index.date).last()
    rets = daily.pct_change()
    if len(daily):
        rets.iloc[0] = daily.iloc[0] / initial - 1
    sharpe = sortino = None
    if len(rets) >= 10 and rets.std(ddof=1) > 0:
        ann = math.sqrt(market.trading_days_per_year)
        sharpe = rets.mean() / rets.std(ddof=1) * ann
        downside = math.sqrt((np.minimum(rets.to_numpy(), 0) ** 2).mean())
        sortino = rets.mean() / downside * ann if downside > 0 else None
    annual = (final / initial) ** (365.25 / span_days) - 1 if span_days >= 180 and final > 0 else None

    net = final - initial
    metrics = {
        "initial_capital": _r(initial),
        "final_equity": _r(final),
        "net_profit": _r(net),
        "net_profit_pct": _r(100 * net / initial),
        "buy_hold_pct": _r(100 * (bh[-1] / initial - 1)) if n else None,
        "annual_return_pct": _r(100 * annual) if annual is not None else None,
        "max_drawdown": _r(max_dd_abs),
        "max_drawdown_pct": _r(max_dd_pct),
        "return_over_max_dd": _r(net / max_dd_abs) if max_dd_abs > 0 else None,
        "total_trades": len(trades),
        "winning_trades": int(len(wins)),
        "losing_trades": int(len(losses)),
        "win_rate": _r(100 * len(wins) / len(trades)) if trades else None,
        "gross_profit": _r(gross_profit),
        "gross_loss": _r(gross_loss),
        "profit_factor": _r(gross_profit / gross_loss) if gross_loss > 0 else None,
        "avg_trade": _r(pnl.mean()) if trades else None,
        "avg_trade_pct": _r(np.mean([t.pnl_pct for t in trades])) if trades else None,
        "avg_win": _r(wins.mean()) if len(wins) else None,
        "avg_loss": _r(losses.mean()) if len(losses) else None,
        "win_loss_ratio": _r(wins.mean() / -losses.mean()) if len(wins) and len(losses) else None,
        "largest_win": _r(wins.max()) if len(wins) else None,
        "largest_loss": _r(losses.min()) if len(losses) else None,
        "max_consecutive_wins": _streak(pnl > 0),
        "max_consecutive_losses": _streak(pnl < 0),
        "avg_bars_in_trade": _r(np.mean([t.exit_bar - t.entry_bar for t in trades]), 1) if trades else None,
        "exposure_pct": _r(100 * np.mean(np.asarray(broker.position_curve[:n]) != 0)) if n else None,
        "commission_paid": _r(broker.commission_paid),
        "sharpe": _r(sharpe),
        "sortino": _r(sortino),
    }

    # curves, thinned for the browser
    step = max(1, math.ceil(n / MAX_CURVE_POINTS))
    idx = list(range(0, n, step))
    if n and idx[-1] != n - 1:
        idx.append(n - 1)
    ms = epoch_ms(times)
    curve = {
        "t": [int(ms[i]) for i in idx],
        "equity": [_r(eq[i]) for i in idx],
        "buy_hold": [_r(bh[i]) for i in idx],
        "drawdown_pct": [_r(dd_pct[i]) for i in idx],
    }

    monthly = []
    if span_days >= 45:
        month_end = eq_s.groupby([eq_s.index.year, eq_s.index.month]).last()
        prev = initial
        for (y, m), v in month_end.items():
            monthly.append({"year": int(y), "month": int(m), "return_pct": _r(100 * (v / prev - 1))})
            prev = v

    trade_rows = [{
        "n": k + 1,
        "side": "Long" if t.direction == LONG else "Short",
        "entry_id": t.entry_id,
        "exit_reason": t.exit_reason if t.exit_reason not in ("close",) else "Close",
        "exit_id": t.exit_id,
        "entry_time": _fmt_time(t.entry_time, market.timezone, intraday),
        "exit_time": _fmt_time(t.exit_time, market.timezone, intraday),
        "entry_price": _r(t.entry_price, 6),
        "exit_price": _r(t.exit_price, 6),
        "qty": _r(t.qty, 6),
        "pnl": _r(t.pnl),
        "pnl_pct": _r(t.pnl_pct),
        "commission": _r(t.commission),
        "bars": t.exit_bar - t.entry_bar,
    } for k, t in enumerate(trades)]

    return {
        "metrics": metrics,
        "long": _side_stats([t for t in trades if t.direction == LONG]),
        "short": _side_stats([t for t in trades if t.direction != LONG]),
        "curve": curve,
        "monthly": monthly,
        "trades": trade_rows[-MAX_TRADES:],
        "trades_truncated": len(trade_rows) > MAX_TRADES,
        "window": {
            "start": _fmt_time(int(ms[0]), market.timezone, True) if n else None,
            "end": _fmt_time(int(ms[-1]), market.timezone, True) if n else None,
            "bars": n,
            "warmup_bars": start,
        },
    }


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r.get("status") == "ok"]
    rets = [r["metrics"]["net_profit_pct"] for r in ok if r["metrics"]["net_profit_pct"] is not None]
    bh = [r["metrics"]["buy_hold_pct"] for r in ok if r["metrics"]["buy_hold_pct"] is not None]
    trades = sum(r["metrics"]["total_trades"] for r in ok)
    wins = sum(r["metrics"]["winning_trades"] for r in ok)
    best = max(ok, key=lambda r: r["metrics"]["net_profit_pct"] or -1e18, default=None)
    worst = min(ok, key=lambda r: r["metrics"]["net_profit_pct"] or 1e18, default=None)
    return {
        "instruments_tested": len(ok),
        "instruments_failed": len(results) - len(ok),
        "profitable": sum(1 for x in rets if x > 0),
        "avg_return_pct": _r(np.mean(rets)) if rets else None,
        "median_return_pct": _r(np.median(rets)) if rets else None,
        "avg_buy_hold_pct": _r(np.mean(bh)) if bh else None,
        "beat_buy_hold": sum(1 for r in ok if (r["metrics"]["net_profit_pct"] or 0) > (r["metrics"]["buy_hold_pct"] or 0)),
        "avg_max_drawdown_pct": _r(np.mean([r["metrics"]["max_drawdown_pct"] for r in ok])) if ok else None,
        "total_trades": trades,
        "win_rate": _r(100 * wins / trades) if trades else None,
        "best": {"symbol": best["symbol"], "name": best["name"], "return_pct": best["metrics"]["net_profit_pct"]} if best else None,
        "worst": {"symbol": worst["symbol"], "name": worst["name"], "return_pct": worst["metrics"]["net_profit_pct"]} if worst else None,
    }
