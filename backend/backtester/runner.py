"""Backtest one strategy on one instrument.

`run_isolated` runs it in a separate process with a time limit, so an uploaded
strategy that loops forever or crashes cannot take the server down with it.
"""
from __future__ import annotations

import multiprocessing as mp
import traceback

import pandas as pd

from .data import epoch_ms
from .engine.broker import Broker, BrokerConfig
from .markets import Instrument, Market, Period, get_market, get_period
from .pine.compiler import Declaration, compile_pine
from .pine.runtime import PineError, Runtime
from .strategy import StrategyError, StrategySpec
from . import python_strategy, report

TIME_LIMIT_SECONDS = 180


def broker_config(market: Market, instrument: Instrument, decl: Declaration | None) -> tuple[BrokerConfig, dict]:
    """Broker settings: the market's defaults, overridden by a Pine script's strategy(...) settings."""
    d = decl or Declaration()
    capital = d.initial_capital if d.initial_capital and d.initial_capital > 0 else market.initial_capital
    cur = market.currency_symbol

    if d.default_qty_type is None and d.default_qty_value is None:
        qty_type, qty_value = "percent_of_equity", 100.0
        sizing = "100 % of equity per trade"
    else:
        qty_type = d.default_qty_type if d.default_qty_type in ("fixed", "cash", "percent_of_equity") else "fixed"
        qty_value = d.default_qty_value if d.default_qty_value is not None else 1.0
        sizing = {"fixed": f"{qty_value:g} units per trade", "cash": f"{cur}{qty_value:,.0f} per trade",
                  "percent_of_equity": f"{qty_value:g} % of equity per trade"}[qty_type]

    if d.commission_value:
        v, ctype = d.commission_value, d.commission_type or "percent"
        if ctype == "cash_per_order":
            commission = lambda notional, qty, side: v
            cost_note = f"{cur}{v:g} per order (from the script)"
        elif ctype == "cash_per_contract":
            commission = lambda notional, qty, side: v * qty
            cost_note = f"{cur}{v:g} per unit (from the script)"
        else:
            commission = lambda notional, qty, side: notional * v / 100
            cost_note = f"{v:g} % per fill (from the script)"
    else:
        commission = lambda notional, qty, side: market.commission(notional, side)
        cost_note = market.cost_note

    slip_ticks = d.slippage if d.slippage else 0.0
    cfg = BrokerConfig(
        initial_capital=capital, commission=commission,
        slippage=0.0 if slip_ticks else market.slippage_bps / 10_000, slippage_ticks=slip_ticks,
        mintick=instrument.mintick, fractional=market.fractional, qty_type=qty_type, qty_value=qty_value,
        pyramiding=max(1, d.pyramiding), process_orders_on_close=d.process_orders_on_close,
    )
    settings = {
        "initial_capital": capital, "currency": market.currency, "currency_symbol": cur,
        "sizing": sizing, "costs": cost_note,
        "pyramiding": max(1, d.pyramiding),
        "fills": "Orders fill on the bar's close" if d.process_orders_on_close
        else "Orders fill at the next bar's open; stops and targets fill intrabar",
    }
    if slip_ticks:
        settings["costs"] += f"; slippage {slip_ticks:g} ticks (from the script)"
    return cfg, settings


def simulate(candles: pd.DataFrame, start: int, broker: Broker, on_bar) -> None:
    o, h, l, c = (candles[k].tolist() for k in ("open", "high", "low", "close"))
    t = epoch_ms(candles.index).tolist()
    for i in range(len(candles)):
        broker.active = i >= start
        broker.begin_bar(i, t[i], o[i], h[i], l[i], c[i])
        on_bar(i)
        broker.end_bar()
    broker.finish()


def run_instrument(spec: StrategySpec, market_id: str, period_id: str, symbol: str,
                   candles: pd.DataFrame, start: int) -> dict:
    market, period = get_market(market_id), get_period(period_id)
    instrument = next(i for i in market.instruments if i.symbol == symbol)
    warnings: list[str] = []
    inputs: dict = {}

    if spec.kind == "pine":
        compiled = compile_pine(spec.source)
        cfg, settings = broker_config(market, instrument, compiled.declaration)
        broker = Broker(cfg)
        session_open = (market.session[0].hour, market.session[0].minute) if market.session else None
        rt = Runtime(candles, broker, symbol=symbol, mintick=instrument.mintick, sym_type=instrument.kind,
                     currency=market.currency, timezone=market.timezone, bar_minutes=period.bar_minutes,
                     session_open=session_open, version=compiled.version)
        bar = compiled.bind(rt)

        def on_bar(i: int) -> None:
            rt.begin(i)
            bar()
            rt.commit()

        try:
            simulate(candles, start, broker, on_bar)
        except PineError as exc:
            raise StrategyError(f"Runtime error on bar {rt.i}: {exc.message}", exc.line) from None
        except Exception as exc:
            msg = f"Runtime error on bar {rt.i}: {type(exc).__name__}: {exc}"
            raise StrategyError(msg, compiled.pine_line(exc)) from None
        warnings += compiled.warnings + sorted(rt.warnings)
        inputs = rt.inputs
    else:
        cfg, settings = broker_config(market, instrument, None)
        broker = Broker(cfg)
        fn = python_strategy.load_function(spec.source, spec.filename, spec.entry_point)
        data = candles.copy()
        signals = python_strategy.normalize(python_strategy.call(fn, data), data.index)
        if python_strategy.lookahead_suspected(fn, data, signals, start):
            warnings.append("Possible look-ahead bias: the signals for past bars changed when more bars were "
                            "added. Check for shift(-n), centred windows or whole-series statistics.")
        simulate(candles, start, broker, python_strategy.driver(signals, broker))

    if broker.skipped_orders:
        warnings.append(f"{broker.skipped_orders} entries were skipped because the position size rounded to "
                        "zero (not enough capital for one unit).")
    body = report.build(broker, candles, start, market, period.bar_minutes < 1440)
    return {"status": "ok", "symbol": symbol, "name": instrument.name, "settings": settings,
            "inputs": inputs, "warnings": warnings, **body}


def _child(conn, args) -> None:
    try:
        conn.send(run_instrument(*args))
    except StrategyError as exc:
        conn.send({"status": "error", "error": exc.message, "line": exc.line})
    except Exception as exc:
        tb = traceback.extract_tb(exc.__traceback__)
        user = [f for f in tb if f.filename == args[0].filename]
        line = user[-1].lineno if user else None
        conn.send({"status": "error", "error": f"{type(exc).__name__}: {exc}", "line": line})
    finally:
        conn.close()


def run_isolated(spec: StrategySpec, market_id: str, period_id: str, symbol: str, candles: pd.DataFrame,
                 start: int, timeout: float = TIME_LIMIT_SECONDS) -> dict:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(child, (spec, market_id, period_id, symbol, candles, start)),
                       daemon=True)
    proc.start()
    child.close()
    try:
        if parent.poll(timeout):
            return parent.recv()
        return {"status": "error", "error": f"The strategy took longer than {int(timeout)} s on this instrument "
                                            "and was stopped."}
    except EOFError:
        return {"status": "error", "error": "The strategy process crashed (out of memory or a fatal error)."}
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)
        parent.close()
