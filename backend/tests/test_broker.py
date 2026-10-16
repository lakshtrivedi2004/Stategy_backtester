"""Order fills follow TradingView's broker emulator."""
import pytest

from backtester.runner import simulate

from conftest import broker, frame


def run(rows, script, start=0, **kw):
    b = broker(**kw)
    simulate(frame(rows), start, b, lambda i: script(b, i))
    return b


FLAT = (100, 101, 99, 100)


def test_market_entry_fills_at_next_open():
    b = run([FLAT, (102, 103, 101, 102), FLAT],
            lambda b, i: b.entry("L", "long") if i == 0 else None)
    t = b.trades[0]
    assert (t.entry_bar, t.entry_price) == (1, 102)
    assert t.exit_reason == "End of test" and t.exit_price == 100


def test_stop_that_gaps_fills_at_the_open():
    def script(b, i):
        if i == 0:
            b.entry("L", "long")
            b.exit("SL", "L", stop=95)
    b = run([FLAT, FLAT, (90, 91, 88, 89)], script)
    t = b.trades[0]
    assert (t.exit_bar, t.exit_price, t.exit_reason) == (2, 90, "Stop")


def test_intrabar_path_order():
    # high is further from the open than the low: open -> low -> high -> close, so the stop hits first
    def script(b, i):
        if i == 0:
            b.entry("L", "long")
            b.exit("X", "L", stop=98, limit=104)
    b = run([FLAT, FLAT, (100, 105, 97.5, 101)], script)
    assert b.trades[0].exit_reason == "Stop"
    # low further than high: open -> high -> low -> close, so the target hits first
    b = run([FLAT, FLAT, (100, 104.5, 95, 101)], script)
    assert b.trades[0].exit_reason == "Target" and b.trades[0].exit_price == 104


def test_entry_in_opposite_direction_reverses():
    def script(b, i):
        if i == 0:
            b.entry("L", "long")
        if i == 2:
            b.entry("S", "short")
    b = run([FLAT, FLAT, FLAT, (98, 99, 97, 98), FLAT], script)
    assert b.trades[0].exit_id == "S" and b.trades[0].exit_price == 98
    assert b.trades[1].direction == -1 and b.trades[1].entry_price == 98


@pytest.mark.parametrize("pyramiding, lots", [(1, 1), (3, 3)])
def test_pyramiding_limits_entries(pyramiding, lots):
    b = run([FLAT] * 6, lambda b, i: b.entry("L", "long") if i < 4 else None, pyramiding=pyramiding)
    assert len(b.trades) == lots


def test_percent_of_equity_sizing_and_costs():
    b = run([FLAT, (100, 101, 99, 110), FLAT], lambda b, i: b.entry("L", "long") if i == 0 else None,
            qty_type="percent_of_equity", qty_value=50, slippage=0.001,
            commission=lambda notional, qty, side: notional * 0.001)
    t = b.trades[0]
    assert t.entry_price == pytest.approx(100.1)               # slippage on the buy
    assert t.qty == pytest.approx(5000 / 100.1)
    assert t.commission == pytest.approx(0.001 * t.qty * (100.1 + 100))
    assert b.realized == pytest.approx(t.pnl)


def test_tick_based_profit_target():
    def script(b, i):
        if i == 0:
            b.entry("L", "long")
            b.exit("TP", "L", profit=200)                      # 200 ticks * 0.01 = 2.00
    b = run([FLAT, FLAT, (100, 103, 99.5, 101)], script)
    assert b.trades[0].exit_price == pytest.approx(102)


def test_process_orders_on_close():
    b = run([FLAT, (100, 101, 99, 100.5), FLAT], lambda b, i: b.entry("L", "long") if i == 1 else None,
            process_orders_on_close=True)
    assert (b.trades[0].entry_bar, b.trades[0].entry_price) == (1, 100.5)


def test_orders_ignored_during_warmup():
    b = run([FLAT] * 5, lambda b, i: b.entry("L", "long"), start=3)
    assert len(b.trades) == 1 and b.trades[0].entry_bar == 4
    assert len(b.equity_curve) == 2


def test_close_and_trailing_stop():
    def script(b, i):
        if i == 0:
            b.entry("L", "long")
            b.exit("Trail", "L", trail_points=100, trail_offset=100)   # activate at +1.00, trail 1.00
    rows = [FLAT, (100, 100.5, 99, 100), (100, 104, 99.8, 104), (104, 104.5, 102, 103)]
    b = run(rows, script)
    t = b.trades[0]
    assert t.exit_reason == "Trailing stop" and t.exit_price == pytest.approx(103)
