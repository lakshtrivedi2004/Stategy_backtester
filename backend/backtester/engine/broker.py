"""Bar-by-bar broker emulator shared by Python and Pine Script strategies.

It follows TradingView's strategy semantics, so Pine scripts behave as they do there:

* A strategy runs on the close of each bar. Market orders it places fill at the
  open of the next bar, or at that bar's close with `process_orders_on_close`.
* Stop and limit orders, including `strategy.exit` brackets, are checked against
  each later bar along an assumed intrabar path: open → high → low → close if the
  high is nearer the open, otherwise open → low → high → close. A price that gaps
  through an order fills at the open. A stop wins a tie with a limit.
* Entering in the opposite direction reverses the position. Same-direction
  entries are limited by `pyramiding`.
* Market and stop fills pay slippage; limit fills don't. Every fill pays the
  market's commission (or the script's own commission settings).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

NA = float("nan")
LONG, SHORT = 1, -1


def _num(x) -> float | None:
    """Pine-style optional number: na / None -> None."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def direction_of(d) -> int:
    if d is True or d == 1 or (isinstance(d, str) and d.lower() in ("long", "buy")):
        return LONG
    if d is False or d == -1 or (isinstance(d, str) and d.lower() in ("short", "sell")):
        return SHORT
    raise ValueError(f"Order direction must be long or short, got {d!r}")


@dataclass
class Lot:
    entry_id: str
    direction: int
    qty: float
    price: float
    bar: int
    time: int
    commission: float
    comment: str = ""


@dataclass
class Trade:
    entry_id: str
    exit_id: str
    direction: int
    qty: float
    entry_bar: int
    entry_time: int
    entry_price: float
    exit_bar: int
    exit_time: int
    exit_price: float
    pnl: float
    pnl_pct: float
    commission: float
    exit_reason: str


@dataclass
class EntryOrder:
    id: str
    direction: int
    qty: float | None
    limit: float | None
    stop: float | None
    comment: str = ""


@dataclass
class ExitOrder:
    id: str
    from_entry: str
    qty: float | None
    qty_percent: float | None
    profit: float | None       # ticks
    limit: float | None        # price
    loss: float | None         # ticks
    stop: float | None         # price
    trail_price: float | None
    trail_points: float | None
    trail_offset: float | None
    created_bar: int
    trail_active: bool = False
    trail_extreme: float = NA


@dataclass
class BrokerConfig:
    initial_capital: float
    commission: Callable[[float, float, str], float]   # (notional, qty, side) -> charges
    slippage: float = 0.0                           # fraction of price, e.g. 0.0002
    slippage_ticks: float = 0.0
    mintick: float = 0.01
    fractional: bool = True
    qty_type: str = "percent_of_equity"            # percent_of_equity | cash | fixed
    qty_value: float = 100.0
    pyramiding: int = 1
    process_orders_on_close: bool = False
    allow_long: bool = True
    allow_short: bool = True


class Broker:
    def __init__(self, cfg: BrokerConfig):
        self.cfg = cfg
        self.lots: list[Lot] = []
        self.trades: list[Trade] = []
        self.realized = 0.0               # closed P&L after commissions
        self.commission_paid = 0.0
        self.entries: dict[str, EntryOrder] = {}     # pending market / limit / stop entries
        self.closes: dict[str, tuple[str | None, float | None]] = {}  # key -> (entry id or None=all, qty%)
        self.exits: dict[str, ExitOrder] = {}
        self.active = False               # orders are ignored during warm-up
        self.bar = -1
        self.time = 0
        self.open = self.high = self.low = self.close = NA
        self.equity_curve: list[float] = []
        self.position_curve: list[float] = []
        self.skipped_orders = 0
        self.max_equity = cfg.initial_capital
        self.max_drawdown = 0.0

    # ------------------------------------------------------------------ state

    @property
    def position_size(self) -> float:
        return sum(l.direction * l.qty for l in self.lots)

    @property
    def position_avg_price(self) -> float:
        q = sum(l.qty for l in self.lots)
        return sum(l.qty * l.price for l in self.lots) / q if q else NA

    @property
    def openprofit(self) -> float:
        return sum(l.direction * l.qty * (self.close - l.price) for l in self.lots) if self.lots else 0.0

    @property
    def netprofit(self) -> float:
        return self.realized

    @property
    def equity(self) -> float:
        return self.cfg.initial_capital + self.realized + self.openprofit

    @property
    def position_entry_name(self) -> str:
        return self.lots[0].entry_id if self.lots else ""

    def _dir(self) -> int:
        p = self.position_size
        return LONG if p > 0 else SHORT if p < 0 else 0

    # ----------------------------------------------------------- order API

    def entry(self, id, direction, qty=None, limit=None, stop=None, comment="", when=True, **_):
        if not self.active or not when:
            return
        d = direction_of(direction)
        self.entries[str(id)] = EntryOrder(str(id), d, _num(qty), _num(limit), _num(stop), comment or "")
        if self.cfg.process_orders_on_close and _num(limit) is None and _num(stop) is None:
            self._fill_market_entries(self.close, at_close=True)

    def exit(self, id, from_entry="", qty=None, qty_percent=None, profit=None, limit=None, loss=None,
             stop=None, trail_price=None, trail_points=None, trail_offset=None, when=True, **_):
        if not self.active or not when:
            return
        key = f"{id}|{from_entry or ''}"
        prev = self.exits.get(key)
        order = ExitOrder(str(id), str(from_entry or ""), _num(qty), _num(qty_percent), _num(profit),
                          _num(limit), _num(loss), _num(stop), _num(trail_price), _num(trail_points),
                          _num(trail_offset), self.bar)
        if prev is not None:                      # keep trailing state when the script re-issues the order
            order.trail_active, order.trail_extreme = prev.trail_active, prev.trail_extreme
        self.exits[key] = order

    def close_position(self, id=None, immediately=False, qty=None, qty_percent=None, when=True, **_):
        if not self.active or not when:
            return
        pct = _num(qty_percent)
        if _num(qty) is not None:
            held = sum(l.qty for l in self.lots if id is None or l.entry_id == str(id))
            pct = 100.0 * min(1.0, _num(qty) / held) if held else None
        target = None if id is None else str(id)
        if immediately or self.cfg.process_orders_on_close:
            self._close_lots(target, self.close, "close", pct, reason="Close")
        else:
            self.closes[f"close:{target}"] = (target, pct)

    def close_all(self, immediately=False, when=True, **_):
        self.close_position(None, immediately=immediately, when=when)

    def cancel(self, id, when=True, **_):
        if not when:
            return
        self.entries.pop(str(id), None)
        for k in [k for k, o in self.exits.items() if o.id == str(id)]:
            del self.exits[k]

    def cancel_all(self, when=True, **_):
        if when:
            self.entries.clear()
            self.exits.clear()

    # ------------------------------------------------------------- per bar

    def begin_bar(self, i: int, t: int, o: float, h: float, l: float, c: float) -> None:
        """Advance to bar `i` and fill whatever the previous bar's orders trigger."""
        self.bar, self.time = i, t
        self.open, self.high, self.low, self.close = o, h, l, c
        if not (self.entries or self.closes or self.exits):
            return
        # market orders first, at the open
        for target, pct in list(self.closes.values()):
            self._close_lots(target, self._slip(o, -self._dir()), "close", pct, reason="Close")
        self.closes.clear()
        self._fill_market_entries(o)
        # then price orders along the intrabar path
        if h - o <= o - l:
            path = [(o, h), (h, l), (l, c)]
        else:
            path = [(o, l), (l, h), (h, c)]
        for p0, p1 in path:
            self._walk(p0, p1)
        self._update_trailing(h, l)
        self._drop_orphan_exits()

    def end_bar(self) -> None:
        if self.cfg.process_orders_on_close and self.active:
            for target, pct in list(self.closes.values()):
                self._close_lots(target, self.close, "close", pct, reason="Close")
            self.closes.clear()
            self._fill_market_entries(self.close, at_close=True)
        eq = self.equity
        if self.active:
            self.equity_curve.append(eq)
            self.position_curve.append(self.position_size)
            self.max_equity = max(self.max_equity, eq)
            self.max_drawdown = max(self.max_drawdown, self.max_equity - eq)

    def finish(self) -> None:
        """Close anything still open at the last close so every trade is reported."""
        if self.lots:
            self._close_lots(None, self.close, "End of test", None, reason="End of test")
            if self.equity_curve:
                self.equity_curve[-1] = self.equity

    # ------------------------------------------------------------ internals

    def _slip(self, price: float, direction: int) -> float:
        """Adverse slippage for a fill in `direction` (+1 buy, -1 sell)."""
        if direction == 0:
            return price
        return price * (1 + direction * self.cfg.slippage) + direction * self.cfg.slippage_ticks * self.cfg.mintick

    def _qty_for(self, order: EntryOrder, price: float) -> float:
        if order.qty is not None:
            qty = order.qty
        elif self.cfg.qty_type == "fixed":
            qty = self.cfg.qty_value
        elif self.cfg.qty_type == "cash":
            qty = self.cfg.qty_value / price
        else:
            equity = self.cfg.initial_capital + self.realized + sum(
                l.direction * l.qty * (price - l.price) for l in self.lots)
            qty = max(0.0, equity) * self.cfg.qty_value / 100.0 / price
        if not self.cfg.fractional:
            qty = math.floor(qty + 1e-9)
        return qty

    def _fill_market_entries(self, price: float, at_close: bool = False) -> None:
        for key in [k for k, o in self.entries.items() if o.limit is None and o.stop is None]:
            order = self.entries.pop(key)
            fill = price if at_close else self._slip(price, order.direction)
            self._fill_entry(order, fill)

    def _fill_entry(self, order: EntryOrder, price: float) -> bool:
        d = order.direction
        if (d == LONG and not self.cfg.allow_long) or (d == SHORT and not self.cfg.allow_short):
            if self._dir() == -d:                     # disallowed entries only close the opposite side
                self._close_lots(None, price, order.id, None, reason="Reverse")
            return True
        cur = self._dir()
        if cur == -d:
            self._close_lots(None, price, order.id, None, reason="Reverse")
        elif cur == d and len(self.lots) >= max(1, self.cfg.pyramiding):
            return False
        qty = self._qty_for(order, price)
        if qty <= 0:
            self.skipped_orders += 1
            return True
        fee = self.cfg.commission(qty * price, qty, "buy" if d == LONG else "sell")
        self.realized -= fee
        self.commission_paid += fee
        self.lots.append(Lot(order.id, d, qty, price, self.bar, self.time, fee, order.comment))
        return True

    def _close_lots(self, entry_id: str | None, price: float, exit_id: str, pct: float | None,
                    reason: str) -> None:
        keep: list[Lot] = []
        for lot in self.lots:
            if entry_id is not None and lot.entry_id != entry_id:
                keep.append(lot)
                continue
            qty = lot.qty if pct is None else lot.qty * min(100.0, pct) / 100.0
            if not self.cfg.fractional:
                qty = math.floor(qty + 1e-9) if pct is not None else qty
            if qty <= 0:
                keep.append(lot)
                continue
            share = qty / lot.qty
            entry_fee = lot.commission * share
            exit_fee = self.cfg.commission(qty * price, qty, "sell" if lot.direction == LONG else "buy")
            gross = lot.direction * qty * (price - lot.price)
            self.realized += gross - exit_fee
            self.commission_paid += exit_fee
            pnl = gross - entry_fee - exit_fee
            self.trades.append(Trade(
                lot.entry_id, exit_id, lot.direction, qty, lot.bar, lot.time, lot.price,
                self.bar, self.time, price, pnl, 100.0 * pnl / (lot.price * qty),
                entry_fee + exit_fee, reason))
            if qty < lot.qty - 1e-12:
                lot.qty -= qty
                lot.commission -= entry_fee
                keep.append(lot)
        self.lots = keep

    # price orders --------------------------------------------------------

    def _exit_levels(self, order: ExitOrder, lots: list[Lot]) -> tuple[float | None, float | None]:
        """(stop, limit) prices for an exit order on these lots."""
        d = lots[0].direction
        q = sum(l.qty for l in lots)
        avg = sum(l.qty * l.price for l in lots) / q
        tick = self.cfg.mintick
        stop, limit = order.stop, order.limit
        if order.loss is not None:
            s = avg - d * order.loss * tick
            stop = s if stop is None else (max(stop, s) if d == LONG else min(stop, s))
        if order.profit is not None:
            t = avg + d * order.profit * tick
            limit = t if limit is None else (min(limit, t) if d == LONG else max(limit, t))
        if order.trail_active and order.trail_offset is not None and not math.isnan(order.trail_extreme):
            ts = order.trail_extreme - d * order.trail_offset * tick
            stop = ts if stop is None else (max(stop, ts) if d == LONG else min(stop, ts))
        return stop, limit

    def _lots_for(self, order: ExitOrder) -> list[Lot]:
        return [l for l in self.lots if not order.from_entry or l.entry_id == order.from_entry]

    @staticmethod
    def _trigger(kind: str, level: float, p0: float, p1: float) -> float | None:
        """Fill price if an order of `kind` at `level` triggers on the move p0 -> p1."""
        if kind in ("buy_stop", "sell_limit"):           # triggers on the way up
            if p0 >= level:
                return p0
            return level if p1 >= level else None
        if p0 <= level:                                  # buy_limit, sell_stop: on the way down
            return p0
        return level if p1 <= level else None

    def _candidates(self, p0: float, p1: float) -> list[tuple[float, int, str, object, float]]:
        out = []
        span = abs(p1 - p0) or 1.0
        for key, o in self.entries.items():
            if o.limit is None and o.stop is None:
                continue
            if self._dir() == o.direction and len(self.lots) >= max(1, self.cfg.pyramiding):
                continue
            if o.stop is not None:
                kind, level = ("buy_stop" if o.direction == LONG else "sell_stop"), o.stop
            else:
                kind, level = ("buy_limit" if o.direction == LONG else "sell_limit"), o.limit
            fill = self._trigger(kind, level, p0, p1)
            if fill is not None:
                out.append((abs(fill - p0) / span, 1, "entry", key, fill))
        for key, o in self.exits.items():
            lots = self._lots_for(o)
            if not lots:
                continue
            d = lots[0].direction
            stop, limit = self._exit_levels(o, lots)
            if stop is not None:
                fill = self._trigger("sell_stop" if d == LONG else "buy_stop", stop, p0, p1)
                if fill is not None:
                    out.append((abs(fill - p0) / span, 0, "stop", key, fill))
            if limit is not None:
                fill = self._trigger("sell_limit" if d == LONG else "buy_limit", limit, p0, p1)
                if fill is not None:
                    out.append((abs(fill - p0) / span, 2, "limit", key, fill))
        out.sort(key=lambda x: (x[0], x[1]))
        return out

    def _walk(self, p0: float, p1: float) -> None:
        for _ in range(50):
            cands = self._candidates(p0, p1)
            if not cands:
                return
            _, _, kind, key, fill = cands[0]
            if kind == "entry":
                order = self.entries.pop(key)
                price = self._slip(fill, order.direction) if order.stop is not None else fill
                self._fill_entry(order, price)
            else:
                order = self.exits[key]
                lots = self._lots_for(order)
                d = lots[0].direction
                price = self._slip(fill, -d) if kind == "stop" else fill
                pct = order.qty_percent
                if order.qty is not None:
                    held = sum(l.qty for l in lots)
                    pct = 100.0 * min(1.0, order.qty / held) if held else None
                reason = "Stop" if kind == "stop" else "Target"
                if kind == "stop" and order.trail_active:
                    reason = "Trailing stop"
                self._close_lots(order.from_entry or None, price, order.id, pct, reason=reason)
                if not self._lots_for(order):
                    del self.exits[key]
            p0 = fill

    def _update_trailing(self, h: float, l: float) -> None:
        for o in self.exits.values():
            if o.trail_offset is None or (o.trail_points is None and o.trail_price is None):
                continue
            lots = self._lots_for(o)
            if not lots:
                continue
            d = lots[0].direction
            best = h if d == LONG else l
            if not o.trail_active:
                avg = sum(x.qty * x.price for x in lots) / sum(x.qty for x in lots)
                act = o.trail_price if o.trail_price is not None else avg + d * o.trail_points * self.cfg.mintick
                if (d == LONG and h >= act) or (d == SHORT and l <= act):
                    o.trail_active, o.trail_extreme = True, best
            else:
                o.trail_extreme = max(o.trail_extreme, best) if d == LONG else min(o.trail_extreme, best)

    def _drop_orphan_exits(self) -> None:
        for key in list(self.exits):
            o = self.exits[key]
            if self._lots_for(o) or o.created_bar >= self.bar - 1:
                continue
            waiting = any(not o.from_entry or e.id == o.from_entry for e in self.entries.values())
            if not waiting:
                del self.exits[key]
