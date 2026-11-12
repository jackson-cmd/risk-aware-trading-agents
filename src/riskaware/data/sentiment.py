"""CNN Fear & Greed index (live only, informational).

The agent's own fear-and-greed gauge is computed from price data so it also
exists in backtests (see ``agents.macro``). This module only fetches CNN's
published number to show next to it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..utils import BROWSER_UA, DiskCache, make_session, safe_float

log = logging.getLogger(__name__)

CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"


@dataclass
class FearGreedReading:
    score: float
    rating: str
    previous_close: float | None = None
    previous_week: float | None = None
    previous_month: float | None = None
    source: str = "CNN"


def fetch_cnn_fear_greed(cache: DiskCache | None = None, timeout: float = 15.0) -> FearGreedReading | None:
    def _get():
        s = make_session(BROWSER_UA, retries=1)
        s.headers.update({"Referer": "https://edition.cnn.com/", "Origin": "https://edition.cnn.com",
                          "Accept": "application/json"})
        try:
            r = s.get(CNN_URL, timeout=timeout)
            if not r.ok:
                log.info("CNN Fear & Greed unavailable: HTTP %s", r.status_code)
                return None
            fg = r.json().get("fear_and_greed") or {}
            return {
                "score": safe_float(fg.get("score")),
                "rating": fg.get("rating", ""),
                "previous_close": safe_float(fg.get("previous_close")),
                "previous_week": safe_float(fg.get("previous_1_week")),
                "previous_month": safe_float(fg.get("previous_1_month")),
            }
        except Exception as exc:
            log.info("CNN Fear & Greed fetch failed: %s", exc)
            return None

    data = cache.memo_json("sentiment", "cnn_fear_greed", 3600, _get) if cache else _get()
    if not data or data.get("score") is None:
        return None
    return FearGreedReading(
        score=float(data["score"]), rating=str(data.get("rating", "")),
        previous_close=data.get("previous_close"), previous_week=data.get("previous_week"),
        previous_month=data.get("previous_month"),
    )
