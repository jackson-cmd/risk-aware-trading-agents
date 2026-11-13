"""Background jobs: they run outside the page, report progress, can be stopped, and keep their result."""

import threading
import time

import pytest

from riskaware.dashboard.jobs import JobBoard


def wait_done(board, kind, timeout=5.0):
    t0 = time.time()
    while board.get(kind).running:
        assert time.time() - t0 < timeout, "job did not finish"
        time.sleep(0.01)
    return board.get(kind)


def test_a_job_runs_in_the_background_and_keeps_its_result():
    board = JobBoard()
    gate = threading.Event()

    def work(ctx):
        ctx.report(0.5, "half way")
        gate.wait(2)
        return {"answer": 42}

    job = board.start("scan", work, "Scan")
    time.sleep(0.05)
    assert job.running and board.running("scan") and job.progress == 0.5 and job.message == "half way"
    with pytest.raises(RuntimeError):  # one job of a kind at a time
        board.start("scan", work, "Scan again")
    other = board.start("news", lambda ctx: "other kinds run alongside", "News")
    gate.set()
    job = wait_done(board, "scan")
    assert job.ok and job.result == {"answer": 42} and job.progress == 1.0 and job.elapsed() >= 0
    assert wait_done(board, "news").result == "other kinds run alongside" and other.id != job.id
    assert [j.kind for j in board.all()] == ["scan", "news"]
    assert board.mark_seen("scan") is True and board.mark_seen("scan") is False  # new only once
    assert board.start("scan", lambda ctx: 1, "Scan").id > job.id  # a finished kind can start again


def test_errors_and_stop():
    board = JobBoard()
    board.start("backtest", lambda ctx: 1 / 0, "Backtest")
    job = wait_done(board, "backtest")
    assert not job.ok and job.error.startswith("ZeroDivisionError") and "1 / 0" in job.trace

    def long(ctx):
        for i in range(500):
            ctx.report(i / 500)
            time.sleep(0.01)
        return "finished"

    board.start("backtest", long, "Backtest")
    time.sleep(0.05)
    assert board.stop("backtest") is True
    job = wait_done(board, "backtest")
    assert job.cancelled and not job.ok and job.result is None
    assert board.stop("backtest") is False and board.stop("nothing") is False
