"""The strategy lab: AI-written code is checked for look-ahead and side effects, then run in its own process."""

import json
import subprocess
import sys
from datetime import date

import pandas as pd
import pytest
from typer.testing import CliRunner

from riskaware import indicators as ind
from riskaware import lab, mcp_server
from riskaware.cli import app
from riskaware.data.prices import SyntheticPrices

HEAD = "from riskaware.strategy import Action, Signal, Strategy\n\n\nclass S(Strategy):\n    name = 's'\n\n"


def strat(body: str, head: str = HEAD) -> str:
    """A strategy whose on_bar runs ``body`` (indented by the caller's 8 spaces) and returns no signals."""
    return head + "    def on_bar(self, ctx):\n" + body + "        return []\n"


def problems(code: str) -> list[str]:
    return [p["problem"] for p in lab.check_code(code, ["AAA", "BBB"])]


def test_the_guide_examples_pass_and_the_guide_lists_the_indicators():
    g = lab.guide()
    assert lab.check_code(g["template"]) == [] and lab.check_code(g["news_example"]) == []
    assert {"close", "ema200", "rsi14", "mom126"} <= set(g["indicators"])
    assert "ctx.news(symbol)" in g["ctx"] and len(g["rules"]) >= 4


@pytest.mark.parametrize("body, words", [
    ("        if ctx.day == date(2026, 4, 9):\n            pass\n", "fixed date"),
    ("        x = '2026-04-09'\n", "date written in a string"),
    ("        x = 'event 20260409 rally'\n", "date written in a string"),
    ("        if ctx.day.year == 2026:\n            pass\n", ".year"),
    ("        x = ctx.day.today()\n", ".today"),
    ("        x = ctx.row('NVDA')\n", "names a stock"),
    ("        x = ctx.row('$TSLA')\n", "names a stock"),
    ("        x = ctx._news\n", "private attributes"),
    ("        x = ctx.__class__\n", "double-underscore"),
    ("        x = open('/etc/hosts')\n", "'open' is not allowed"),
    ("        x = getattr(ctx, 'row')\n", "'getattr' is not allowed"),
    ("        x = eval('1')\n", "'eval' is not allowed"),
    ("        h = ctx.history('AAA', 5)\n        h.to_csv('x.csv')\n", "'.to_csv'"),
    ("        x = '{0.row}'.format(ctx)\n", "'.format'"),
    ("        g = (i for i in [1])\n        x = g.gi_frame\n", "'.gi_frame'"),
])
def test_look_ahead_and_side_effects_are_refused(body, words):
    found = problems(strat(body))
    assert any(words in p for p in found), found


@pytest.mark.parametrize("head, words", [
    ("import os\n" + HEAD, "import of 'os'"),
    ("import pandas as pd\n" + HEAD, None),
    ("from riskaware.strategy import load_strategy\n" + HEAD, "not load_strategy"),
    ("from riskaware.news.history import NewsArchive\n" + HEAD, "not allowed"),
    ("import riskaware\n" + HEAD, "not 'import riskaware'"),
    ("from pandas import *\n" + HEAD, "import *"),
])
def test_imports(head, words):
    found = problems(strat("", head))
    if words is None:
        assert found == []
    else:
        assert any(words in p for p in found), found


def test_clean_code_passes_and_market_gauges_and_self_state_are_fine():
    body = ("        spy = ctx.row('SPY')\n"
            "        self._seen = getattr_free = 1\n"
            "        if ctx.day.month == 12 and ctx.day.weekday() == 4:\n            pass\n")
    head = HEAD.replace("    name = 's'\n", "    name = 's'\n\n    def __init__(self, **kw):\n"
                                           "        super().__init__(**kw)\n        self._entries = {}\n")
    code = strat(body, head)
    probs, warns = lab.review_code(code, ["AAA"])
    assert probs == []
    assert any("calendar" in w["warning"] for w in warns)  # month == 12: allowed, but reported


def test_structure_errors():
    assert any("exactly one class" in p for p in problems("x = 1\n"))
    assert any("on_bar" in p for p in problems(HEAD + "    pass\n"))
    assert any("syntax error" in p for p in problems("def (:\n"))


def child(job: dict, timeout: float = 120) -> dict:
    """Run a job in the child process exactly as backtest_code does, without the parent's check."""
    proc = subprocess.run([sys.executable, "-m", "riskaware.lab", "--child"], input=json.dumps(job),
                          capture_output=True, text=True, timeout=timeout, env=lab._child_env(), check=False)
    return json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])


def job(code: str, **kw) -> dict:
    base = {"code": code, "symbols": ["AAA", "BBB"], "start": "2020-03-02", "end": "2020-06-30", "synthetic": True,
            "news": False, "compare": [], "timeout": 120}
    base.update(kw)
    return base


def test_the_runtime_guard_blocks_what_slips_past_the_check(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("1 2 3\n")
    reads = strat(f"        import numpy as np\n        np.loadtxt({str(secret)!r})\n",
                  "import numpy as np\n" + HEAD)
    out = child(job(reads, _skip_check=True))
    assert out["ok"] is False and "may not open files" in out["error"]
    net = strat("        import pandas as pd\n        pd.read_csv('https://example.com/prices.csv')\n",
                "import pandas as pd\n" + HEAD)
    out = child(job(net, _skip_check=True))
    assert out["ok"] is False and "may not" in out["error"]
    imp = strat("        import os\n")
    out = child(job(imp, _skip_check=True))
    assert out["ok"] is False and "import of 'os' is not allowed" in out["error"]


def test_the_child_gets_no_api_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "x")
    env = lab._child_env()
    assert "OPENAI_API_KEY" not in env and "ALPACA_SECRET_KEY" not in env and env["PYTHONPATH"]


def test_backtest_code_end_to_end_with_errors_timeouts_and_saving(tmp_path, monkeypatch):
    g = lab.guide()
    out = lab.backtest_code(g["template"], symbols=["AAA", "BBB", "CCC"], start="2020-01-02", end="2020-12-31",
                            synthetic=True, news=False, timeout=120)
    assert out["ok"] and out["strategy"] == "dip_in_uptrend" and "buy_and_hold" in out["compare"]
    assert out["metrics"]["total_return"] is not None and out["trades"]["count"] >= 0 and out["look_ahead"]
    refused = lab.backtest_code(strat("        x = ctx.row('NVDA')\n"), symbols=["AAA"], synthetic=True)
    assert refused["ok"] is False and refused["stage"] == "check" and refused["problems"][0]["line"] == 8
    broken = strat("        print('day', ctx.day.month)\n        x = 1 / 0\n")
    out = lab.backtest_code(broken, symbols=["AAA"], start="2020-03-02", end="2020-04-30", synthetic=True,
                            news=False, timeout=120)
    assert out["ok"] is False and "ZeroDivisionError" in out["error"] and out["where"][0]["line"] == 9
    assert "day" in (out["printed"] or "")
    slow = strat("        while True:\n            pass\n")
    out = lab.backtest_code(slow, symbols=["AAA"], start="2020-03-02", end="2020-04-30", synthetic=True,
                            news=False, timeout=8)
    assert out["ok"] is False and "longer than" in out["error"]
    monkeypatch.setattr(lab, "AI_DIR", tmp_path / "ai")
    out = lab.backtest_code(g["template"], symbols=["AAA"], start="2020-03-02", end="2020-06-30", synthetic=True,
                            news=False, save_as="dip_test", timeout=120)
    assert out["ok"] and (tmp_path / "ai" / "dip_test.py").read_text() == g["template"]
    assert lab.saved_strategies()[0]["name"] == "dip_test"
    assert lab.backtest_code(g["template"], symbols=["AAA"], synthetic=True, save_as="Bad Name")["ok"] is False


def test_mcp_tools_and_cli():
    names = [f.__name__ for f in mcp_server.TOOLS]
    assert "strategy_guide" in names and "backtest_code" in names
    assert "strategy_guide" in mcp_server.READ_ONLY and "backtest_code" not in mcp_server.READ_ONLY
    assert "saved" in mcp_server.list_strategies()
    res = CliRunner().invoke(app, ["lab", "--check-only", "--symbols", "AAA", "/dev/null"])
    assert res.exit_code == 1  # empty file: no Strategy class


def test_indicators_use_no_later_data():
    """Row t computed from data up to t equals row t computed from the whole history."""
    d0, d1 = date(2018, 1, 1), date(2020, 12, 31)
    fr = SyntheticPrices(seed=11, start=d0, end=d1).history(["AAA", "SPY"], d0, d1)
    full = ind.compute(fr["AAA"], fr["SPY"]["close"])
    for cut in (300, 500, 700):
        part = ind.compute(fr["AAA"].iloc[:cut], fr["SPY"]["close"].iloc[:cut])
        pd.testing.assert_series_equal(part.iloc[-1], full.iloc[cut - 1], check_names=False, rtol=1e-9)
