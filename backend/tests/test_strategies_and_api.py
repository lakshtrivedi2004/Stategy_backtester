"""Python strategies, file loading, the per-instrument runner and the HTTP API."""
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backtester import data as data_mod
from backtester.api import app
from backtester.runner import run_instrument
from backtester.strategy import StrategyError, detect_kind, load_strategy

from conftest import make_candles

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def spec_of(name: str, source: str):
    return load_strategy(name, source.encode())


def run(source: str, name="s.py", market="CRYPTO", start=100, n=600):
    return run_instrument(spec_of(name, source), market, "1Y", "BTC-USD" if market == "CRYPTO" else "^NSEI",
                          make_candles(n), start)


def test_position_series_strategy():
    r = run("def generate_signals(data):\n    return (data['close'] > data['close'].rolling(20).mean()).astype(int)\n")
    assert r["status"] == "ok" and r["metrics"]["total_trades"] > 5
    assert r["short"]["trades"] == 0
    assert r["warnings"] == []


def test_signal_frame_with_stop_and_target():
    src = """
import pandas as pd
def generate_signals(data, params):
    out = pd.DataFrame(index=data.index)
    out['signal'] = 0
    out.iloc[150, 0] = 1
    out['stop_loss'] = data['close'] * 0.5
    out['take_profit'] = data['close'] * 1.02
    return out
"""
    r = run(src)
    reasons = {t["exit_reason"] for t in r["trades"]}
    assert r["metrics"]["total_trades"] == 1 and reasons <= {"Target", "End of test"}


def test_lookahead_is_flagged():
    src = "def generate_signals(data):\n    return (data['close'].shift(-1) > data['close']).astype(int)\n"
    r = run(src)
    assert any("look-ahead" in w for w in r["warnings"])


def test_whole_lot_sizing_for_indian_market():
    r = run("def generate_signals(data):\n    return data['close'] * 0 + 1\n", market="INDIA")
    assert all(float(t["qty"]).is_integer() for t in r["trades"])


@pytest.mark.parametrize("filename, source, kind", [
    ("a.py", "x=1", "python"), ("a.pine", "x=1", "pine"),
    ("a.txt", "//@version=5\nstrategy('x')", "pine"), ("a.txt", "import pandas\ndef f(): pass", "python"),
])
def test_detect_kind(filename, source, kind):
    assert detect_kind(filename, source) == kind


def test_python_without_entry_point_is_rejected():
    with pytest.raises(StrategyError, match="generate_signals"):
        spec_of("s.py", "def foo(data):\n    return data\n")
    with pytest.raises(StrategyError) as exc:
        spec_of("s.py", "def generate_signals(data)\n    return 1\n")
    assert exc.value.line == 1


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("*")), ids=lambda p: p.name)
def test_examples_run(path):
    r = run_instrument(load_strategy(path.name, path.read_bytes()), "CRYPTO", "1Y", "BTC-USD", make_candles(1500), 500)
    assert r["status"] == "ok" and r["metrics"]["total_trades"] > 0


@pytest.fixture
def client(monkeypatch):
    def fake_history(instrument, market, period):
        return data_mod.History(make_candles(700, seed=len(instrument.symbol)), 200)
    monkeypatch.setattr("backtester.jobs.load_history", fake_history)
    return TestClient(app)


def test_options(client):
    o = client.get("/api/options").json()
    assert [m["id"] for m in o["markets"]] == ["FOREX", "CRYPTO", "INDIA"]
    assert [p["id"] for p in o["periods"]][:3] == ["1D", "1W", "1M"]
    assert {e["filename"] for e in o["examples"]} >= {"ema_crossover.pine"}


def test_bad_upload_is_rejected_with_line(client):
    r = client.post("/api/backtests", data={"market": "FOREX", "period": "1M"},
                    files={"file": ("bad.pine", b'//@version=5\nstrategy("x")\nx = ta.nope(close)\n')})
    assert r.status_code == 422 and r.json()["detail"]["line"] == 3
    r = client.post("/api/backtests", data={"market": "MARS", "period": "1M"}, files={"file": ("a.py", b"x")})
    assert r.status_code == 422


def test_full_backtest_job(client):
    src = (EXAMPLES / "ema_crossover.pine").read_bytes()
    r = client.post("/api/backtests", data={"market": "FOREX", "period": "3M"}, files={"file": ("ema.pine", src)})
    assert r.status_code == 202
    job_id = r.json()["id"]
    for _ in range(300):
        v = client.get(f"/api/backtests/{job_id}").json()
        if v["status"] != "running":
            break
        time.sleep(0.1)
    assert v["status"] == "done", v
    rep = v["report"]
    assert rep["strategy"]["name"] == "EMA Crossover with ATR stop"
    assert rep["summary"]["instruments_tested"] == 5
    assert all(p["status"] == "done" for p in v["progress"])
    first = rep["instruments"][0]
    assert first["window"]["start"].startswith("2025")
    assert len(first["curve"]["t"]) == len(first["curve"]["equity"]) == 500
