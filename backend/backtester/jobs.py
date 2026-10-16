"""Background backtest jobs, kept in memory only (nothing is stored on disk)."""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import report
from .data import DataError, load_history
from .markets import INTERVAL_LABELS, Market, Period
from .runner import run_isolated
from .strategy import StrategySpec

MAX_JOBS = 20
_slots = threading.BoundedSemaphore(max(2, min(5, os.cpu_count() or 2)))


class Job:
    def __init__(self, spec: StrategySpec, market: Market, period: Period):
        self.id = uuid.uuid4().hex[:12]
        self.spec, self.market, self.period = spec, market, period
        self.created = datetime.now(timezone.utc)
        self.status = "running"
        self.error: str | None = None
        self.progress = {i.symbol: {"symbol": i.symbol, "name": i.name, "status": "queued"}
                         for i in market.instruments}
        self.report: dict | None = None
        self.lock = threading.Lock()

    def set_progress(self, symbol: str, status: str) -> None:
        with self.lock:
            self.progress[symbol]["status"] = status

    def view(self) -> dict:
        with self.lock:
            return {"id": self.id, "status": self.status, "error": self.error,
                    "progress": list(self.progress.values()), "report": self.report}


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()

    def start(self, spec: StrategySpec, market: Market, period: Period) -> Job:
        job = Job(spec, market, period)
        with self.lock:
            self.jobs[job.id] = job
            for old in sorted(self.jobs.values(), key=lambda j: j.created)[:-MAX_JOBS]:
                self.jobs.pop(old.id, None)
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def _one(self, job: Job, instrument) -> dict:
        base = {"symbol": instrument.symbol, "name": instrument.name}
        job.set_progress(instrument.symbol, "downloading")
        try:
            history = load_history(instrument, job.market, job.period)
        except DataError as exc:
            job.set_progress(instrument.symbol, "error")
            return {**base, "status": "error", "error": str(exc)}
        job.set_progress(instrument.symbol, "testing")
        with _slots:
            result = run_isolated(job.spec, job.market.id, job.period.id, instrument.symbol,
                                  history.candles, history.start_index)
        job.set_progress(instrument.symbol, "done" if result.get("status") == "ok" else "error")
        return {**base, **result}

    def _run(self, job: Job) -> None:
        try:
            with ThreadPoolExecutor(len(job.market.instruments)) as pool:
                results = list(pool.map(lambda ins: self._one(job, ins), job.market.instruments))
            spec, period = job.spec, job.period
            body = {
                "id": job.id,
                "created_at": job.created.isoformat(),
                "market": job.market.to_dict(),
                "period": {"id": period.id, "label": period.label, "interval": period.interval,
                           "interval_label": INTERVAL_LABELS[period.interval]},
                "strategy": {"name": spec.name, "language": spec.language, "filename": spec.filename},
                "summary": report.summarize(results),
                "instruments": results,
            }
            ok = any(r.get("status") == "ok" for r in results)
            with job.lock:
                job.report = body
                job.status = "done" if ok else "error"
                if not ok:
                    errors = {r.get("error") for r in results}
                    job.error = errors.pop() if len(errors) == 1 else "The backtest failed on every instrument."
        except Exception as exc:  # pragma: no cover - defensive
            with job.lock:
                job.status, job.error = "error", f"{type(exc).__name__}: {exc}"


jobs = JobManager()
