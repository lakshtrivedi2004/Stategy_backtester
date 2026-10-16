# Strategy Backtester

Upload a trading strategy, pick a market and how much history to test, press **Start**, and get a performance report.

1. **Market**: Forex, Crypto or the Indian market. Each market is tested on 5 liquid instruments:

   | Market | Instruments | Costs per fill |
   |---|---|---|
   | Forex | EUR/USD, GBP/USD, USD/JPY, AUD/USD, USD/CAD | 0.002 % commission + 0.5 bp slippage |
   | Crypto | BTC, ETH, SOL, BNB, XRP (vs USD) | 0.10 % taker fee + 2 bp slippage |
   | Indian market | NIFTY 50, NIFTY BANK, Reliance, HDFC Bank, Infosys | brokerage ₹20 / 0.03 %, STT, exchange, SEBI, stamp duty, GST + 2 bp slippage |

2. **History**: 1D, 1W, 1M, 3M, 6M, 1Y, 2Y, 5Y or 10Y. The bar size follows from it, within Yahoo Finance's limits:

   | History | 1D | 1W | 1M | 3M – 1Y | 2Y – 10Y |
   |---|---|---|---|---|---|
   | Bars | 1-minute | 5-minute | 15-minute | 1-hour | daily |

   Up to 500 bars before the window are loaded as warm-up, so indicators already have values when testing starts.

3. **Strategy file**: a TradingView **Pine Script** strategy (`.pine`, v4/v5/v6) or a **Python** file (`.py`).

The report has the results for each instrument, an equity curve against buy & hold, the drawdown, monthly returns, trade statistics, a long/short split and the full trade list, which you can download as CSV. Nothing is stored: reports live in server memory until the server restarts.

> Historical research only. The app never places orders. Backtest results are hypothetical.

## Quick start

```bash
make setup     # creates venv/, installs backend + frontend dependencies
make dev       # API on :8000, site on http://localhost:5173
```

To run the API and the built site as one process on <http://localhost:8000>, use `make serve`. You can also use `docker compose up`. To run the tests, use `make test`.

## Writing a strategy

Example files are in [examples/](examples/), and the upload screen links to them.

### Pine Script

Upload the strategy as you would paste it into TradingView. It must call `strategy(...)` and place orders with `strategy.entry`, `strategy.order`, `strategy.exit`, `strategy.close` or `strategy.close_all`.

- **Settings from `strategy(...)`**: `initial_capital`, `default_qty_type` / `default_qty_value`, `commission_type` / `commission_value`, `slippage`, `pyramiding` and `process_orders_on_close` are honoured. If the script sets no position size, each trade uses 100 % of equity. If it sets no commission, the market's costs in the table above apply.
- **Inputs** use their default values. Plots, colours, labels, tables and alerts are ignored.
- **Language features**: `if`/`else`, `switch`, `for`, `while`, `var`, tuples, history references (`x[1]`) and user functions, which keep separate `ta.*` state per call site as in Pine.
- **Built-ins**: most of `ta.*` (moving averages, RSI, ATR, MACD, Bollinger, Keltner, Supertrend, DMI/ADX, SAR, pivots, `valuewhen`, `barssince`, VWAP …), `math.*`, `nz`/`na`, `time()` with sessions, `timeframe.change`, and `strategy.*` state such as `position_size` and `position_avg_price`.
- **`request.security`** on the same symbol reads real higher-timeframe bars for price series (`close`, `high[1]` …). Calculated expressions fall back to the chart timeframe, and the report flags this.
- **Not supported yet**: arrays, matrices, maps, `import`ed libraries, user-defined types and methods. The upload is rejected with the line number of the first unsupported feature.

### Python

Define `generate_signals(data)`. `data` is a DataFrame indexed by bar time with the columns `open, high, low, close, volume`. Return one of these:

- a **Series** of the position you want after each bar: `1` long, `-1` short, `0` flat, `NaN` = keep the current position:

  ```python
  def generate_signals(data):
      fast = data["close"].ewm(span=9).mean()
      slow = data["close"].ewm(span=21).mean()
      return (fast > slow).astype(int)          # long when the fast EMA is above the slow one, else flat
  ```

- a **DataFrame** with a `signal` column (`1` buy, `-1` sell short, `0` nothing). It can also have `exit` / `exit_long` / `exit_short` (True closes the position) and `stop_loss` / `take_profit` (absolute prices for the new position).

Set `NAME = "..."` to name the strategy in the report. The backtester reruns your function on truncated data. If past signals change once later bars are added, the report warns about look-ahead bias.

## How fills work

These rules apply to both languages and match TradingView's broker emulator:

- Signals are evaluated on the close of a bar, and market orders fill at the next bar's open.
- Stops, limits and `strategy.exit` brackets are checked within each bar along an assumed path: open → high → low → close, or open → low → high → close if the low is nearer the open. A price that gaps through an order fills at the open.
- An entry in the opposite direction reverses the position.
- Market and stop fills pay slippage, and every fill pays the market's fees.
- Positions still open at the end are closed at the last close.
- Indian-market quantities are whole shares. Forex and crypto can trade fractions.

Each instrument runs in its own process with a 3-minute limit, so a broken strategy can't hang the server.

## Project layout

```text
backend/backtester/
  markets.py          markets, instruments, costs and history periods
  data.py             Yahoo Finance download, cleaning, warm-up window
  engine/broker.py    bar-by-bar order engine (fills, brackets, sizing, costs)
  pine/               Pine Script lexer/parser, compiler to Python, runtime (ta.*, strategy.*)
  python_strategy.py  runs Python strategies, look-ahead check
  strategy.py         detects and validates the uploaded file
  runner.py           one strategy x one instrument, in an isolated process
  report.py           metrics and report data
  jobs.py, api.py     background jobs and the HTTP API
backend/tests/        indicator accuracy, Pine semantics, broker fills, API
frontend/             React + Vite + Tailwind site (ECharts)
examples/             sample Pine and Python strategies
```

## Security note

Python strategies are executed as normal Python code, in a separate process with a time limit. That protects the server from hangs and crashes, not from malicious code. Only run strategy files you trust, and don't expose the server to untrusted users. Pine Script is compiled from a parsed syntax tree and cannot run arbitrary Python.
