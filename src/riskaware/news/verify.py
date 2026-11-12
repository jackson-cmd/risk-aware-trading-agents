"""Cross-source news verification.

Headlines are grouped into stories (clusters). A story counts as CONFIRMED only
when at least two independent publishers carry it, or PRIMARY when the
company's own SEC filing backs it. Single-publisher stories get a small weight,
opinion pieces get none, and an unconfirmed red flag can block a buy but can
never trigger a sell. One planted headline therefore cannot move the agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import NewsConfig
from ..types import NewsCluster, NewsItem, NewsStatus
from . import lexicon as lx

SEC_EVENT_TYPES = {
    "2.02": "earnings", "1.01": "mna", "2.01": "mna", "5.02": "management", "1.03": "legal",
    "3.01": "legal", "4.02": "legal", "4.01": "management", "2.05": "management", "2.06": "earnings",
}


@dataclass
class NewsReport:
    symbol: str
    clusters: list[NewsCluster]
    score: float
    confirmed: int
    unconfirmed: int
    opinion: int
    red_flags_confirmed: list[str]
    red_flags_unconfirmed: list[str]
    severe_confirmed: bool
    total_items: int
    sources: dict[str, str] = field(default_factory=dict)
    off_topic: int = 0

    @property
    def has_news(self) -> bool:
        return self.total_items > 0

    def top(self, n: int = 5) -> list[NewsCluster]:
        order = {NewsStatus.PRIMARY: 0, NewsStatus.CONFIRMED: 1, NewsStatus.UNCONFIRMED: 2, NewsStatus.OPINION: 3}
        return sorted(
            self.clusters,
            key=lambda c: (order[c.status], -(c.last_seen or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
        )[:n]


def _sec_codes(item: NewsItem) -> list[str]:
    if item.feed != "sec" or not item.summary.startswith("items:"):
        return []
    return [c for c in item.summary[6:].split(",") if c]


def _item_events(item: NewsItem) -> set[str]:
    codes = _sec_codes(item)
    if codes:
        return {SEC_EVENT_TYPES[c] for c in codes if c in SEC_EVENT_TYPES}
    return set(lx.event_types(item.title))


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, str]] = set()
    out = []
    for it in items:
        key_title = (lx.normalize_publisher(it.publisher), " ".join(sorted(lx.tokens(it.title))))
        if it.url and it.url in seen_urls:
            continue
        if key_title in seen_titles:
            continue
        if it.url:
            seen_urls.add(it.url)
        seen_titles.add(key_title)
        out.append(it)
    return out


def cluster_items(items: list[NewsItem], threshold: float = 0.34, window_hours: float = 72) -> list[list[NewsItem]]:
    """Single-linkage clustering on title similarity, shared event types and time proximity."""
    n = len(items)
    if n == 0:
        return []
    toks = [lx.tokens(i.title) for i in items]
    # tokens shared by most headlines (company name, ticker) carry no story information
    if n >= 6:
        df: dict[str, int] = {}
        for t in toks:
            for w in t:
                df[w] = df.get(w, 0) + 1
        common = {w for w, c in df.items() if c / n > 0.5}
        toks = [t - common for t in toks]
    events = [_item_events(i) for i in items]
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            ti, tj = items[i].published, items[j].published
            hours = abs((ti - tj).total_seconds()) / 3600 if ti and tj else 0.0
            if hours > window_hours:
                continue
            shared_events = events[i] & events[j]
            if items[i].feed == "sec" or items[j].feed == "sec":
                if shared_events and hours <= 36 and items[i].feed != items[j].feed:
                    union(i, j)
                continue
            a, b = toks[i], toks[j]
            if not a or not b:
                continue
            jac = len(a & b) / len(a | b)
            if jac >= threshold or (jac >= threshold * 0.6 and shared_events and hours <= 24):
                union(i, j)
    groups: dict[int, list[NewsItem]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())


def classify_cluster(items: list[NewsItem], min_sources: int = 2) -> NewsCluster:
    opinions = []
    facts = []
    for it in items:
        if it.feed != "sec" and (lx.is_opinion(it.title) or it.summary.startswith("analysis")):
            opinions.append(it)
        else:
            facts.append(it)
    basis = facts or opinions
    publishers = sorted({lx.normalize_publisher(i.publisher) for i in basis})
    families = {lx.publisher_family(p) for p in publishers}
    feeds = sorted({i.feed for i in items})
    has_sec = any(i.feed == "sec" for i in facts)

    if not facts:
        status = NewsStatus.OPINION
    elif has_sec:
        status = NewsStatus.PRIMARY
    elif len(families) >= min_sources:
        status = NewsStatus.CONFIRMED
    else:
        status = NewsStatus.UNCONFIRMED

    weights, scores = [], []
    flags: list[str] = []
    severe = False
    for it in basis:
        codes = _sec_codes(it)
        if codes:
            vals = [lx.SEC_ITEM_EVENTS[c][1] for c in codes if c in lx.SEC_ITEM_EVENTS]
            s = min(vals) if vals and min(vals) < 0 else (max(vals) if vals else 0.0)
            for c in codes:
                flag = lx.SEC_ITEM_EVENTS.get(c, (None, 0.0, None))[2]
                if flag:
                    flags.append(flag)
                    severe = True
            w = 1.0
        else:
            text = f"{it.title}. {it.summary if not it.summary.startswith('analysis') else ''}"
            s = lx.sentiment(text)
            f, sev = lx.red_flags(it.title)
            flags.extend(f)
            severe = severe or sev
            w = lx.reliability(lx.normalize_publisher(it.publisher))
        weights.append(w)
        scores.append(s)
    sentiment = sum(w * s for w, s in zip(weights, scores, strict=True)) / sum(weights) if weights else 0.0
    best = max(basis, key=lambda i: (lx.reliability(lx.normalize_publisher(i.publisher)), -(i.published or datetime.max.replace(tzinfo=timezone.utc)).timestamp()))
    times = [i.published for i in items if i.published]
    ev: set[str] = set()
    for it in items:
        ev |= _item_events(it)
    return NewsCluster(
        headline=best.title, items=items, publishers=publishers, feeds=feeds, status=status,
        sentiment=max(-1.0, min(1.0, sentiment)), event_types=sorted(ev), red_flags=sorted(set(flags)),
        severe=severe, first_seen=min(times) if times else None, last_seen=max(times) if times else None,
    )


def analyze_news(symbol: str, items: list[NewsItem], cfg: NewsConfig | None = None,
                 now: datetime | None = None, sources: dict[str, str] | None = None,
                 names: list[str] | None = None) -> NewsReport:
    """Cluster, verify and score the news for one symbol.

    With ``names`` (see ``relevance.company_names``) stories that name neither the company nor its ticker are
    dropped first and counted in ``off_topic``.
    """
    cfg = cfg or NewsConfig()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg.lookback_hours)
    fresh = [i for i in items if i.published is None or i.published >= cutoff]
    fresh = _dedupe(fresh)
    off_topic = 0
    if names is not None and cfg.relevance_filter:
        from .relevance import is_relevant

        kept = [i for i in fresh if is_relevant(i, symbol, names)]
        off_topic, fresh = len(fresh) - len(kept), kept
    clusters = [classify_cluster(g, cfg.min_independent_sources)
                for g in cluster_items(fresh, cfg.similarity_threshold, cfg.lookback_hours)]
    num = den = 0.0
    flags_c: set[str] = set()
    flags_u: set[str] = set()
    severe_c = False
    for c in clusters:
        if c.status == NewsStatus.OPINION:
            continue
        w = 1.0 if c.confirmed else cfg.unconfirmed_weight
        age_h = (now - c.last_seen).total_seconds() / 3600 if c.last_seen else 24.0
        decay = 0.5 ** (max(age_h, 0.0) / 48.0)
        num += w * decay * c.sentiment
        den += w * decay
        if c.confirmed:
            flags_c |= set(c.red_flags)
            severe_c = severe_c or c.severe
        else:
            flags_u |= set(c.red_flags)
    score = num / max(1.0, den)
    return NewsReport(
        symbol=symbol, clusters=clusters, score=max(-1.0, min(1.0, score)),
        confirmed=sum(c.confirmed for c in clusters),
        unconfirmed=sum(c.status == NewsStatus.UNCONFIRMED for c in clusters),
        opinion=sum(c.status == NewsStatus.OPINION for c in clusters),
        red_flags_confirmed=sorted(flags_c), red_flags_unconfirmed=sorted(flags_u - flags_c),
        severe_confirmed=severe_c, total_items=len(fresh), sources=dict(sources or {}), off_topic=off_topic,
    )
