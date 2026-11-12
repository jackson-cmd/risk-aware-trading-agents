"""The news a backtest saw, prepared for the dashboard (no Streamlit here, so it can be tested on its own)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from .. import i18n
from .. import marketcal as mc
from ..news import lexicon as lx

CONFIRMED = {"PRIMARY", "ISSUER", "CONFIRMED"}


def _l(lang: str, en: str, zh: str) -> str:
    return zh if lang == "zh" else en


def decision_day(fill_day: date) -> date:
    """A backtest order fills on the trading day after the close at which it was decided."""
    return mc.prev_trading_day(fill_day)


def trade_picks(trades: pd.DataFrame | None, symbols: list[str], lang: str = "en", limit: int = 300,
                count: Callable[[str, date], int] | None = None) -> dict[str, tuple[str, date]]:
    """Trades in stocks that have news, newest first: label -> (symbol, the close it was decided at).

    ``count(symbol, day)`` adds how many news items the backtest had seen at that close to each label."""
    out: dict[str, tuple[str, date]] = {}
    if trades is None or len(trades) == 0 or "symbol" not in trades:
        return out
    have = set(symbols)
    for row in trades.iloc[::-1].to_dict("records"):
        sym = row.get("symbol")
        if sym not in have:
            continue
        fill = pd.Timestamp(row["date"]).date()
        reason = str(row.get("reason") or "")[:70]
        decided = decision_day(fill)
        label = f"{fill:%Y-%m-%d} · {row.get('side', '')} {sym}" + (f" · {reason}" if reason else "")
        if count is not None:
            n = count(sym, decided)
            label += _l(lang, f" · {n} news" if n else " · no news", f" · {n} 条新闻" if n else " · 没有新闻")
        out.setdefault(label, (sym, decided))
        if len(out) >= limit:
            break
    return out


def orders_from(trades: pd.DataFrame | None, symbol: str, day: date) -> pd.DataFrame:
    """The orders in ``symbol`` decided at the close of ``day`` (they fill on the next trading day)."""
    if trades is None or len(trades) == 0 or "symbol" not in trades:
        return pd.DataFrame()
    fills = pd.to_datetime(trades["date"]).dt.date
    keep = [c for c in ("date", "symbol", "side", "qty", "price", "reason") if c in trades]
    out = trades[(trades["symbol"] == symbol) & (fills == mc.next_trading_day(day))][keep].copy()
    if "date" in out:
        out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out


def story_rows(report: Any, lang: str = "en", limit: int = 60) -> list[dict[str, Any]]:
    """One row per story, newest first: when it was first seen (New York time), status, sentiment, sources, flags."""
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    stories = sorted(report.clusters, key=lambda c: c.first_seen or c.last_seen or oldest, reverse=True)
    rows = []
    for c in stories[:limit]:
        status = ("✅ " if c.status.value in CONFIRMED else "· ") + i18n.t("news.status." + c.status.value.lower(), lang)
        if c.denial:
            status += " · " + i18n.t("news.denial", lang)
        flags = ", ".join(c.live_flags)
        if c.cleared_flags:
            flags += f" ({i18n.t('news.denied', lang)}: {', '.join(c.cleared_flags)})"
        when = c.first_seen or c.last_seen
        shown: dict[str, str] = {}  # the verifier compares lower-case names; show them as the source wrote them
        for it in c.items:
            shown.setdefault(lx.normalize_publisher(it.publisher), it.publisher)
        rows.append({
            _l(lang, "New York time", "美东时间"): when.astimezone(mc.NY).strftime("%m-%d %H:%M") if when else "",
            _l(lang, "Status", "状态"): status,
            _l(lang, "Sentiment", "情绪"): round(c.sentiment, 2),
            _l(lang, "Headline", "标题"): c.headline,
            _l(lang, "Sources", "来源"): ", ".join(shown.get(p, p) for p in c.publishers[:5]),
            _l(lang, "Flags", "风险"): flags,
        })
    return rows
