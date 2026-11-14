"""Blind trading mode: headlines may not give the date or the price level away."""

from __future__ import annotations

import re

from riskaware.research import Experiment
from riskaware.research.agent import Blind, run_policy
from tests.conftest import needs_news

YEAR = re.compile(r"(?<![\d$.,])(?:19|20)\d{2}(?!\d)")
MONTH_DAY = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d{1,2}\b")


def test_a_blind_headline_hides_names_people_products_dates_and_price_levels():
    b = Blind.make(["TSLA", "MSFT"], aliases={"TSLA": "S02", "MSFT": "S01"})
    b.base.update({"TSLA": 300.0, "MSFT": 250.0})
    assert b.headline("TSLA", "Elon Musk's Tesla Shares Spike To Session High Over $900 Level On May 9, 2022") == \
        "the S02 chief's S02 Shares Spike To Session High Over $300.0 Level On [date]"
    assert b.headline("MSFT", "Jefferies Maintains Buy on Microsoft, Lowers Price Target to $325; Xbox Q3 2022") == \
        "Jefferies Maintains Buy on S01, Lowers Price Target to $130.0; [product] Q3 [year]"
    # totals are not per-share prices and stay; other companies in the run get their own alias
    assert b.headline("MSFT", "Microsoft Wins $10B Deal, Beats Tesla; Stock Up 4%") == \
        "S01 Wins $10B Deal, Beats S02; Stock Up 4%"


def _run(blind: bool, check: bool):
    seen: list[dict] = []

    def policy(obs: dict) -> dict:
        seen.append(obs)
        return {"orders": []}

    exp = Experiment(period="2022", start="2022-05-02", end="2022-06-15", universe="core", max_positions=3)
    res = run_policy(policy, exp, decide_every=5, blind=blind, save=False, check=check)
    heads = [h["headline"] for o in seen for s in o["stocks"] for h in (s.get("news") or {}).get("headlines", [])]
    return res, heads


@needs_news
def test_blind_observations_of_2022_carry_no_date_or_price_level():
    _, named = _run(blind=False, check=False)
    res, blind = _run(blind=True, check=True)
    # the named run shows what blind mode has to hide, so the test has teeth
    assert any(YEAR.search(h) for h in named) and any(MONTH_DAY.search(h) for h in named)
    assert any("$325" in h for h in named) and any("$900" in h for h in named)
    assert len(blind) == len(named)
    assert not any(YEAR.search(h) or MONTH_DAY.search(h) for h in blind), [h for h in blind if YEAR.search(h)]
    assert not any("$325" in h or "$900" in h for h in blind)
    # the observations up to each cut day still do not change with a random future
    assert res.leak["verdict"] == "pass"
