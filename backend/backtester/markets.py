"""The three markets a user can pick, and the history periods they can test over.

Each market tests the strategy on a fixed basket of liquid instruments, so the
user only chooses the market. Costs are applied per fill, on the notional value.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class Instrument:
    symbol: str          # Yahoo Finance ticker
    name: str
    mintick: float       # price increment, used by Pine `syminfo.mintick` and tick-based exits
    kind: str            # Pine `syminfo.type`


@dataclass(frozen=True)
class Market:
    id: str
    label: str
    description: str
    timezone: str
    currency: str
    currency_symbol: str
    initial_capital: float
    fractional: bool                 # can trade fractional quantities
    session: tuple[time, time] | None  # None = trades around the clock
    trading_days_per_year: int
    slippage_bps: float              # applied to market and stop fills
    cost_note: str
    instruments: tuple[Instrument, ...]

    def commission(self, notional: float, side: str) -> float:
        """Charges for one fill. `side` is "buy" or "sell"."""
        if self.id == "INDIA":
            return _india_charges(notional, side)
        if self.id == "CRYPTO":
            return notional * 0.001                    # 0.10 % taker fee
        return notional * 0.00002                      # forex: ~0.2 pip ECN commission

    def to_dict(self) -> dict:
        return {
            "id": self.id, "label": self.label, "description": self.description,
            "currency": self.currency, "currency_symbol": self.currency_symbol,
            "initial_capital": self.initial_capital, "cost_note": self.cost_note,
            "instruments": [{"symbol": i.symbol, "name": i.name} for i in self.instruments],
        }


def _india_charges(notional: float, side: str) -> float:
    """NSE equity charges for one order (discount-broker brokerage + statutory levies)."""
    brokerage = min(20.0, notional * 0.0003)
    exchange = notional * 0.0000297
    sebi = notional * 0.000001
    stt = notional * 0.001                              # securities transaction tax, both sides
    stamp = notional * 0.00015 if side == "buy" else 0.0
    gst = 0.18 * (brokerage + exchange + sebi)
    return brokerage + exchange + sebi + stt + stamp + gst


MARKETS: dict[str, Market] = {
    "FOREX": Market(
        id="FOREX", label="Forex", description="Major currency pairs, 24 hours a day, 5 days a week",
        timezone="UTC", currency="USD", currency_symbol="$", initial_capital=10_000.0,
        fractional=True, session=None, trading_days_per_year=260, slippage_bps=0.5,
        cost_note="0.002 % commission per fill + 0.5 bp slippage",
        instruments=(
            Instrument("EURUSD=X", "EUR / USD", 0.00001, "forex"),
            Instrument("GBPUSD=X", "GBP / USD", 0.00001, "forex"),
            Instrument("USDJPY=X", "USD / JPY", 0.001, "forex"),
            Instrument("AUDUSD=X", "AUD / USD", 0.00001, "forex"),
            Instrument("USDCAD=X", "USD / CAD", 0.00001, "forex"),
        ),
    ),
    "CRYPTO": Market(
        id="CRYPTO", label="Crypto", description="Large-cap coins against USD, 24/7",
        timezone="UTC", currency="USD", currency_symbol="$", initial_capital=10_000.0,
        fractional=True, session=None, trading_days_per_year=365, slippage_bps=2.0,
        cost_note="0.10 % taker fee per fill + 2 bp slippage",
        instruments=(
            Instrument("BTC-USD", "Bitcoin", 0.01, "crypto"),
            Instrument("ETH-USD", "Ethereum", 0.01, "crypto"),
            Instrument("SOL-USD", "Solana", 0.01, "crypto"),
            Instrument("BNB-USD", "BNB", 0.01, "crypto"),
            Instrument("XRP-USD", "XRP", 0.0001, "crypto"),
        ),
    ),
    "INDIA": Market(
        id="INDIA", label="Indian market", description="NSE indices and large-cap stocks",
        timezone="Asia/Kolkata", currency="INR", currency_symbol="₹", initial_capital=100_000.0,
        fractional=False, session=(time(9, 15), time(15, 30)), trading_days_per_year=248,
        slippage_bps=2.0,
        cost_note="Brokerage ₹20 or 0.03 %, STT, exchange, SEBI, stamp duty, GST + 2 bp slippage",
        instruments=(
            Instrument("^NSEI", "NIFTY 50", 0.05, "index"),
            Instrument("^NSEBANK", "NIFTY BANK", 0.05, "index"),
            Instrument("RELIANCE.NS", "Reliance Industries", 0.05, "stock"),
            Instrument("HDFCBANK.NS", "HDFC Bank", 0.05, "stock"),
            Instrument("INFY.NS", "Infosys", 0.05, "stock"),
        ),
    ),
}


@dataclass(frozen=True)
class Period:
    id: str
    label: str
    days: int            # length of the tested window
    interval: str        # Yahoo bar interval
    bar_minutes: int
    fetch_days: int      # how far back to download (includes indicator warm-up)


# Bar size follows Yahoo's history limits: 1m ≈ 7 days, 5m/15m ≈ 60 days, 1h ≈ 730 days.
PERIODS: dict[str, Period] = {
    "1D": Period("1D", "1 Day", 1, "1m", 1, 7),
    "1W": Period("1W", "1 Week", 7, "5m", 5, 58),
    "1M": Period("1M", "1 Month", 30, "15m", 15, 58),
    "3M": Period("3M", "3 Months", 91, "1h", 60, 200),
    "6M": Period("6M", "6 Months", 182, "1h", 60, 300),
    "1Y": Period("1Y", "1 Year", 365, "1h", 60, 720),
    "2Y": Period("2Y", "2 Years", 730, "1d", 1440, 3 * 365),
    "5Y": Period("5Y", "5 Years", 1826, "1d", 1440, 6 * 365),
    "10Y": Period("10Y", "10 Years", 3652, "1d", 1440, 11 * 365),
}

INTERVAL_LABELS = {"1m": "1-minute", "5m": "5-minute", "15m": "15-minute", "1h": "1-hour", "1d": "daily"}


def get_market(market_id: str) -> Market:
    try:
        return MARKETS[market_id.upper()]
    except KeyError:
        raise ValueError(f"Unknown market '{market_id}'. Choose one of {', '.join(MARKETS)}.") from None


def get_period(period_id: str) -> Period:
    try:
        return PERIODS[period_id.upper()]
    except KeyError:
        raise ValueError(f"Unknown history period '{period_id}'. Choose one of {', '.join(PERIODS)}.") from None
