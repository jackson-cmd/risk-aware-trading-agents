"""Long dashboard tasks run in background threads, so they keep going when you switch pages or reload.

A scan, a backtest, an agents run or a news fetch is started as a job. The job runs in its own thread, outside
Streamlit's page run, which Streamlit stops whenever you open another page; the result waits on the board until
you come back. The board keeps the latest job of each kind. There is one board per dashboard process, so every
browser tab with the dashboard open sees the same jobs. Nothing here calls Streamlit, so the job functions must not
either (they get a ``Context`` to report progress instead).
"""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class Cancelled(Exception):
    """Raised inside a job when its Stop button was pressed."""


@dataclass
class Job:
    id: int
    kind: str
    label: str
    started: float
    progress: float | None = None  # 0..1, or None when the job cannot tell
    message: str = ""
    finished: float | None = None
    result: Any = None
    error: str | None = None
    trace: str = ""
    cancelled: bool = False
    stop_requested: bool = False
    seen: bool = False  # the job's own page has shown the finished result
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def ok(self) -> bool:
        return self.finished is not None and self.error is None and not self.cancelled

    def elapsed(self, now: float | None = None) -> float:
        return (self.finished or now or time.time()) - self.started


class Context:
    """What a job function gets: a way to report progress, which also stops the job when Stop was pressed."""

    def __init__(self, job: Job):
        self._job = job

    def report(self, progress: float | None = None, message: str | None = None) -> None:
        if self._job.stop_requested:
            raise Cancelled()
        if progress is not None:
            self._job.progress = max(0.0, min(1.0, float(progress)))
        if message is not None:
            self._job.message = message

    @property
    def stop_requested(self) -> bool:
        return self._job.stop_requested


class JobBoard:
    """The latest job of each kind. A kind runs one job at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._ids = itertools.count(1)

    def get(self, kind: str) -> Job | None:
        return self._jobs.get(kind)

    def all(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.started)

    def running(self, kind: str) -> bool:
        job = self._jobs.get(kind)
        return job is not None and job.running

    def start(self, kind: str, fn: Callable[[Context], Any], label: str, **meta: Any) -> Job:
        """Run ``fn(context)`` in a background thread. Raises RuntimeError if a job of this kind is running."""
        with self._lock:
            cur = self._jobs.get(kind)
            if cur is not None and cur.running:
                raise RuntimeError(f"a {kind} job is already running")
            job = Job(next(self._ids), kind, label, time.time(), meta=dict(meta))
            self._jobs[kind] = job
        threading.Thread(target=self._run, args=(job, fn), name=f"riskaware-{kind}-{job.id}", daemon=True).start()
        return job

    @staticmethod
    def _run(job: Job, fn: Callable[[Context], Any]) -> None:
        try:
            job.result = fn(Context(job))
            job.progress = 1.0
        except Cancelled:
            job.cancelled = True
        except Exception as exc:  # noqa: BLE001 - every failure is shown on the job's page
            job.error = f"{type(exc).__name__}: {exc}"
            job.trace = traceback.format_exc(limit=8)
        finally:
            job.finished = time.time()

    def stop(self, kind: str) -> bool:
        """Ask a running job to stop; it stops at its next progress report."""
        job = self._jobs.get(kind)
        if job is None or not job.running:
            return False
        job.stop_requested = True
        return True

    def mark_seen(self, kind: str) -> bool:
        """Note that the job's page showed its finished result. True the first time (the result is new)."""
        job = self._jobs.get(kind)
        if job is None or job.running or job.seen:
            return False
        job.seen = True
        return True
