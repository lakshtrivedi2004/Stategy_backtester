"""HTTP API and static site.

    GET  /api/options              markets, history periods, example strategies
    POST /api/backtests            multipart form: market, period, file -> {id}
    GET  /api/backtests/{id}       status, per-instrument progress and, when done, the report
    GET  /api/examples/{filename}  an example strategy file
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .jobs import jobs
from .markets import INTERVAL_LABELS, MARKETS, PERIODS, get_market, get_period
from .strategy import StrategyError, load_strategy

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
DIST = ROOT / "frontend" / "dist"

app = FastAPI(title="Strategy Backtester", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("BACKTESTER_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"], allow_headers=["*"],
)


def _examples() -> list[dict]:
    if not EXAMPLES.is_dir():
        return []
    out = []
    for p in sorted(EXAMPLES.iterdir()):
        if p.suffix in (".py", ".pine"):
            out.append({"filename": p.name, "language": "Python" if p.suffix == ".py" else "Pine Script"})
    return out


@app.get("/api/options")
def options() -> dict:
    return {
        "markets": [m.to_dict() for m in MARKETS.values()],
        "periods": [{"id": p.id, "label": p.label, "interval": p.interval,
                     "interval_label": INTERVAL_LABELS[p.interval]} for p in PERIODS.values()],
        "examples": _examples(),
    }


@app.post("/api/backtests", status_code=202)
async def create_backtest(market: str = Form(...), period: str = Form(...), file: UploadFile = File(...)) -> dict:
    try:
        m, p = get_market(market), get_period(period)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    data = await file.read()
    try:
        spec = load_strategy(file.filename or "strategy.txt", data)
    except StrategyError as exc:
        raise HTTPException(422, {"message": exc.message, "line": exc.line}) from None
    job = jobs.start(spec, m, p)
    return {"id": job.id, "strategy": {"name": spec.name, "language": spec.language}}


@app.get("/api/backtests/{job_id}")
def get_backtest(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Backtest not found. Results are kept in memory and are lost on restart.")
    return job.view()


@app.get("/api/examples/{filename}")
def example(filename: str):
    names = {e["filename"] for e in _examples()}
    if filename not in names:
        raise HTTPException(404, "No such example")
    return FileResponse(EXAMPLES / filename, media_type="text/plain", filename=filename)


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="site")
