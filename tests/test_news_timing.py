"""examples/news_timing.py: the numbers the website's news-backtesting guide quotes come from the frozen 2026 data,
so they must not change unless that data does."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from .conftest import needs_news

ROOT = Path(__file__).resolve().parent.parent


@needs_news
def test_the_guide_numbers_still_hold():
    spec = importlib.util.spec_from_file_location("news_timing", ROOT / "examples" / "news_timing.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.compare(*mod.load())
    assert result["trades"] == {"calendar date": 444, "publication time": 458}
    assert result["bps per trade"] == {"calendar date": 32.0, "publication time": -6.7}
    assert result["bps per trade by publication"]["calendar date"][True] == 103.0
    assert result["after the close"] == 178
    assert round(result["moved to another day"]["headlines"], 2) == 0.18
    assert round(result["moved to another day"]["sec"], 2) == 0.63
    for guide in ("site/news-backtesting.html", "site/zh/news-backtesting.html"):
        text = (ROOT / guide).read_text(encoding="utf-8")
        assert "+32.0" in text and "-6.7" in text and "+103" in text, guide
