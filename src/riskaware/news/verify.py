"""Cross-source news verification.

Headlines are grouped into stories (clusters), and each story is graded by who
carries it:

* ``PRIMARY``: the company's own SEC 8-K filing.
* ``ISSUER``: the company's own press release on a press-release wire (Business
  Wire, PR Newswire, GlobeNewswire, ...). Outlets that rewrite the release do not
  confirm it, and several wires carrying it are still one source.
* ``CONFIRMED``: at least two independent outlets.
* ``UNCONFIRMED``: a single outlet. ``OPINION``: listicles and "should you buy" pieces.

A red flag on a PRIMARY, ISSUER or CONFIRMED story blocks new buys, and a severe
one pulls the news score down. A severe flag forces a sale only when the company
itself backs it: the 8-K carries the item, or the company's release confirms what
independent outlets reported at least an hour earlier and no denial disputes it. A
release that is the first word on a story, plus the outlets that repeat it, is
exactly what a planted release looks like, so on its own it never forces a sale.
An unconfirmed red flag can block a buy but never triggers a sell.

Denials ("denies the report", "is not filing for Chapter 11", "finds fake
release") form stories of their own and never count as a publisher of what they
deny. A denial clears the red flags of the story it denies when it is at least as
well sourced (a single outlet can clear a single-source rumor; a story from two
outlets or a company release takes the company's own statement or release, or two
outlets reporting the denial). Nothing clears an 8-K, and the company's denial does
not clear allegations made by others (fraud, SEC charges, short-seller reports,
probes, breaches).
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
# A company release backs a severe flag only when independent outlets reported it at least this much earlier.
CONFIRM_LEAD = timedelta(hours=1)
# A denial that calls a release or story fake without naming it answers the red-flag stories of the day before.
HOAX_WINDOW = timedelta(hours=24)
# A story carrying a severe red flag never scores above this, even when its words sound neutral.
SEVERE_SENTIMENT_CAP = -0.5
STATUS_ORDER = {NewsStatus.PRIMARY: 0, NewsStatus.ISSUER: 1, NewsStatus.CONFIRMED: 2, NewsStatus.UNCONFIRMED: 3,
                NewsStatus.OPINION: 4}


@dataclass
class NewsReport:
    symbol: str
    clusters: list[NewsCluster]
    score: float
    confirmed: int
    unconfirmed: int
    opinion: int
    red_flags_confirmed: list[str]  # flags on PRIMARY, ISSUER or CONFIRMED stories: they block new buys
    red_flags_unconfirmed: list[str]  # single-source flags: a small penalty, never a sell
    severe_confirmed: bool  # a severe flag the company itself backs: the built-in strategy sells everything
    total_items: int
    sources: dict[str, str] = field(default_factory=dict)
    off_topic: int = 0
    red_flags_backed: list[str] = field(default_factory=list)  # the flags behind severe_confirmed
    red_flags_denied: list[str] = field(default_factory=list)  # flags withdrawn by a denial

    @property
    def has_news(self) -> bool:
        return self.total_items > 0

    def top(self, n: int = 5) -> list[NewsCluster]:
        return sorted(
            self.clusters,
            key=lambda c: (STATUS_ORDER[c.status], -(c.last_seen or datetime.min.replace(tzinfo=timezone.utc)).timestamp()),
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


def _is_opinion(item: NewsItem) -> bool:
    return item.feed != "sec" and (lx.is_opinion(item.title) or item.summary.startswith("analysis"))


def _is_wire(item: NewsItem) -> bool:
    return lx.is_press_wire(lx.normalize_publisher(item.publisher))


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
    """Single-linkage clustering on title similarity, shared event types and time proximity.

    A denial never joins the story it denies, however similar the words, so it cannot be
    counted as a second publisher of that story."""
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
    denials = [i.feed != "sec" and lx.is_denial(i.title) for i in items]
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
            if denials[i] != denials[j]:
                continue
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
        (opinions if _is_opinion(it) else facts).append(it)
    basis = facts or opinions
    publishers = sorted({lx.normalize_publisher(i.publisher) for i in basis})
    # wires are the company's own channel and SEC EDGAR is the filing itself: neither is an independent outlet
    outlets = {lx.publisher_family(p) for p in publishers
               if not lx.is_press_wire(p) and p not in lx.PRIMARY_PUBLISHERS}
    feeds = sorted({i.feed for i in items})
    has_sec = any(i.feed == "sec" for i in facts)
    denial = bool(facts) and all(i.feed != "sec" and lx.is_denial(i.title) for i in facts)

    if not facts:
        status = NewsStatus.OPINION
    elif has_sec:
        status = NewsStatus.PRIMARY
    elif any(_is_wire(i) for i in facts):
        status = NewsStatus.ISSUER
    elif len(outlets) >= min_sources:
        status = NewsStatus.CONFIRMED
    else:
        status = NewsStatus.UNCONFIRMED

    weights, scores = [], []
    flags: list[str] = []
    filed: list[str] = []  # severe flags the 8-K items themselves carry
    denies: set[str] = set()
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
                    filed.append(flag)
                    severe = True
            w = 1.0
        else:
            text = f"{it.title}. {it.summary if not it.summary.startswith('analysis') else ''}"
            s = lx.sentiment(text)
            f, sev = lx.red_flags(it.title)
            flags.extend(f)
            severe = severe or sev
            denies.update(lx.denied_flags(it.title))
            w = lx.reliability(lx.normalize_publisher(it.publisher))
        weights.append(w)
        scores.append(s)
    sentiment = sum(w * s for w, s in zip(weights, scores, strict=True)) / sum(weights) if weights else 0.0
    if severe:
        sentiment = min(sentiment, SEVERE_SENTIMENT_CAP)
    elif denial and not flags:
        sentiment = max(sentiment, 0.0)  # "finds fake release, asks for a trading halt" is not bad news
    best = max(basis, key=lambda i: (lx.reliability(lx.normalize_publisher(i.publisher)), -(i.published or datetime.max.replace(tzinfo=timezone.utc)).timestamp()))
    times = [i.published for i in items if i.published]
    ev: set[str] = set()
    for it in items:
        ev |= _item_events(it)
    return NewsCluster(
        headline=best.title, items=items, publishers=publishers, feeds=feeds, status=status,
        sentiment=max(-1.0, min(1.0, sentiment)), event_types=sorted(ev), red_flags=sorted(set(flags)),
        severe=severe, first_seen=min(times) if times else None, last_seen=max(times) if times else None,
        denial=denial, denies=sorted(denies) if denial else [],
        backed_flags=sorted(set(filed)) if status == NewsStatus.PRIMARY else [],
    )


def _strength(c: NewsCluster) -> int:
    """3: the 8-K. 2: the company's release or its own statement, or two independent outlets. 1: one outlet."""
    if c.status == NewsStatus.PRIMARY:
        return 3
    if c.status in (NewsStatus.ISSUER, NewsStatus.CONFIRMED):
        return 2
    if c.denial and any(lx.is_company_statement(i.publisher, i.title) for i in c.items):
        return 2
    return 1


def _flags_around(c: NewsCluster, when: datetime | None) -> tuple[set[str], set[str], bool]:
    """The red flags c's headlines asserted up to ``when``, those a press release asserted after it, and
    whether c carried a press release by then."""
    before: set[str] = set()
    company_after: set[str] = set()
    wire_before = False
    for it in c.items:
        if it.feed == "sec" or _is_opinion(it):
            continue
        flags = set(lx.red_flags(it.title)[0])
        if when is None or it.published is None or it.published <= when:
            before |= flags
            wire_before = wire_before or _is_wire(it)
        elif _is_wire(it):
            company_after |= flags
    return before, company_after, wire_before


def _apply_denials(clusters: list[NewsCluster]) -> None:
    """Mark the red flags each denial disputes, and clear them when the denial is at least as well sourced.

    A denial only answers what was published before it. If the company's own release asserts a flag again
    after the denial, the flag stays; outlets that keep repeating a denied story do not revive it."""
    for d in clusters:
        if not d.denial or d.status == NewsStatus.OPINION:
            continue
        hoaxes = [i.title for i in d.items if lx.is_hoax(i.title)]
        about_release = any(lx.rx(r"\breleases?\b").search(t) for t in hoaxes)
        strength = _strength(d)
        for c in clusters:
            if c.denial or c.status in (NewsStatus.PRIMARY, NewsStatus.OPINION) or not c.red_flags:
                continue
            before, revived, wire_before = _flags_around(c, d.last_seen)
            company_flags = (before - revived) - lx.ALLEGATION_FLAGS
            recent = c.first_seen is None or d.first_seen is None or d.first_seen - c.first_seen <= HOAX_WINDOW
            # "finds fake release" answers the recent stories that carry a press release
            whole = bool(hoaxes) and recent and (wire_before or not about_release)
            hit = company_flags if whole else company_flags & set(d.denies)
            if not hit:
                continue
            c.disputed_flags = sorted(set(c.disputed_flags) | hit)
            if strength >= _strength(c):
                c.cleared_flags = sorted(set(c.cleared_flags) | hit)


def _outlets_reporting(clusters: list[NewsCluster], flag: str, until: datetime) -> set[str]:
    """Independent outlets (not wires, filings, opinion or denials) whose own headline reported ``flag`` by ``until``."""
    outlets = set()
    for c in clusters:
        if c.denial:
            continue
        for it in c.items:
            if it.feed == "sec" or it.published is None or it.published > until or _is_opinion(it) or _is_wire(it):
                continue
            if flag in lx.red_flags(it.title)[0]:
                outlets.add(lx.publisher_family(lx.normalize_publisher(it.publisher)))
    return outlets


def _back_company_releases(clusters: list[NewsCluster], min_sources: int) -> None:
    """A company release backs a severe flag about the company's own actions when independent outlets
    reported that flag at least CONFIRM_LEAD before the release. Allegations by others never qualify:
    a release that mentions a short-seller report is a response to it."""
    for c in clusters:
        if c.status != NewsStatus.ISSUER or c.denial:
            continue
        wires = [i for i in c.items if _is_wire(i)]
        times = [i.published for i in wires if i.published]
        if not times:
            continue
        said: set[str] = set()
        for it in wires:
            said |= set(lx.red_flags(it.title)[0])
        candidates = (said & lx.COMPANY_SEVERE_FLAGS) - set(c.disputed_flags)
        released = min(times)
        c.backed_flags = sorted(f for f in candidates
                                if len(_outlets_reporting(clusters, f, released - CONFIRM_LEAD)) >= min_sources)


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
    _apply_denials(clusters)
    _back_company_releases(clusters, cfg.min_independent_sources)
    num = den = 0.0
    flags_c: set[str] = set()
    flags_u: set[str] = set()
    flags_d: set[str] = set()
    backed: set[str] = set()
    for c in clusters:
        if c.status == NewsStatus.OPINION:
            continue
        (flags_c if c.confirmed else flags_u).update(c.live_flags)
        flags_d |= set(c.cleared_flags)
        backed |= set(c.backed_flags)
        if c.cleared:
            continue  # debunked: no weight in the score
        w = 1.0 if c.confirmed else cfg.unconfirmed_weight
        age_h = (now - c.last_seen).total_seconds() / 3600 if c.last_seen else 24.0
        decay = 0.5 ** (max(age_h, 0.0) / 48.0)
        num += w * decay * c.sentiment
        den += w * decay
    score = num / max(1.0, den)
    return NewsReport(
        symbol=symbol, clusters=clusters, score=max(-1.0, min(1.0, score)),
        confirmed=sum(c.confirmed for c in clusters),
        unconfirmed=sum(c.status == NewsStatus.UNCONFIRMED for c in clusters),
        opinion=sum(c.status == NewsStatus.OPINION for c in clusters),
        red_flags_confirmed=sorted(flags_c), red_flags_unconfirmed=sorted(flags_u - flags_c),
        severe_confirmed=bool(backed), total_items=len(fresh), sources=dict(sources or {}), off_topic=off_topic,
        red_flags_backed=sorted(backed), red_flags_denied=sorted(flags_d - flags_c - flags_u),
    )
