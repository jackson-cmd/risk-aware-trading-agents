"""A small finance news lexicon: sentiment terms, red flags, opinion patterns, event types
and publisher reliability. Plain regular expressions, so every decision is explainable."""

from __future__ import annotations

import re

# (pattern, weight). Multi-word phrases come first so they win over single words.
POSITIVE: list[tuple[str, float]] = [
    (r"raises? (full[- ]year |annual |its )?(guidance|outlook|forecast)", 2.0),
    (r"(beats?|tops?|exceeds?|surpass(es)?) (wall street |analysts'? |the )?(estimates|expectations|forecasts|views)", 2.0),
    (r"better[- ]than[- ]expected", 1.5),
    (r"record (revenue|sales|profit|quarter|earnings|results)", 1.5),
    (r"(raises?|lifts?|boosts?) (price target|pt)\b", 1.0),
    (r"upgrade[sd]? (to )?(buy|outperform|overweight)", 1.5),
    (r"\bupgrade[sd]?\b", 1.0),
    (r"(fda|regulatory) approv(al|es|ed)", 1.5),
    (r"(wins?|awarded|secures?) (a )?(\$?[\d.]+ ?(billion|million|bn|m) )?(contract|deal|order)", 1.0),
    (r"(share )?(buyback|repurchase)", 1.0),
    (r"(raises?|increases?|hikes?) (its |quarterly )?dividend", 1.0),
    (r"margin expansion|margins? (expand|widen)", 1.0),
    (r"strong (demand|growth|results|quarter|sales)", 1.0),
    (r"\b(surg(e|es|ed|ing)|soar(s|ed|ing)?|jump(s|ed|ing)?|rall(y|ies|ied|ying)|rebound(s|ed|ing)?|climb(s|ed|ing)?|spik(e|es|ed|ing))\b", 0.8),
    (r"\b(gain(s|ed)?|ris(e|es|ing)|rose|higher|upbeat|bullish|outperform(s|ed)?|accelerat(es|ed|ing))\b", 0.5),
    (r"\b(partnership|expands?|launch(es)?|breakthrough|milestone)\b", 0.4),
]

NEGATIVE: list[tuple[str, float]] = [
    (r"(cuts?|lowers?|slashes?|reduces?|withdraws?) (full[- ]year |annual |its )?(guidance|outlook|forecast)", 2.0),
    (r"(miss(es)?|falls? short of|below) (wall street |analysts'? |the )?(estimates|expectations|forecasts|views)", 2.0),
    (r"worse[- ]than[- ]expected|weak (guidance|outlook|forecast|demand|results|quarter)", 1.5),
    (r"(cuts?|lowers?|slashes?) (price target|pt)\b", 1.0),
    (r"downgrade[sd]? (to )?(sell|underperform|underweight|neutral|hold)", 1.5),
    (r"\bdowngrade[sd]?\b", 1.0),
    (r"profit warning|warns? (on|of)", 1.5),
    (r"\bbankrupt(cy)?\b|\b(chapter|chap\.?|ch\.) ?(7|11)\b|going[- ]concern|\bfraud\b|delist(ed|ing)?|"
     r"\brestat(e|es|ed|ing|ement)\b", 2.0),
    (r"(dividend cut|cuts? (its )?dividend|suspends? (its )?dividend)", 1.5),
    (r"\b(lawsuit|sued|class action|antitrust|probe|investigation|subpoena|fined|penalty)\b", 1.0),
    (r"\b(recall|halts?|halted|delays?|delayed|outage|breach)\b", 0.8),
    (r"\b(layoffs?|job cuts|restructuring)\b", 0.6),
    (r"\b(plung(e|es|ed|ing)|plummet(s|ed|ing)?|tumbl(e|es|ed|ing)|sink(s|ing)?|sank|slump(s|ed|ing)?|crash(es|ed|ing)?|tank(s|ed|ing)?|crater(s|ed|ing)?)\b", 1.0),
    (r"\b(fall(s|ing)?|fell|drop(s|ped|ping)?|slid(e|es|ing)?|declin(e|es|ed|ing)|lower|bearish|loss widens|slowdown|underperform(s|ed)?)\b", 0.5),
]

# Severe red flags block new buys. They force an exit only when the company itself backs them: its own
# 8-K, or its own press release confirming what independent outlets reported first (see verify.py).
SEVERE_RED_FLAGS: dict[str, str] = {
    "fraud": r"\bfraud(ulent)?\b|ponzi",
    "accounting": r"accounting (irregularit|scandal|errors?)|non-reliance|restat(e|es|ed|ement)",
    "going_concern": r"going[- ]concern",
    "bankruptcy": r"\bbankrupt(cy)?\b|\b(chapter|chap\.?|ch\.) ?(7|11)\b",
    "delisting": r"\bdelist(ed|ing)?\b|deficiency notice",
    "sec_charges": r"sec (charges|sues)|charged by the sec|indict(ed|ment)|criminal (charges|probe|investigation)",
    "auditor": r"auditor (resign|quits|dismiss)|material weakness",
    "short_report": r"short[- ]seller report|hindenburg|muddy waters|short report alleg",
}

MODERATE_RED_FLAGS: dict[str, str] = {
    "investigation": r"\b(sec|doj|ftc|justice department) (probe|investigation|inquiry|subpoena)|under investigation",
    "guidance_cut": r"(cuts?|lowers?|slashes?|withdraws?) (full[- ]year |annual |its )?(guidance|outlook|forecast)",
    "exec_departure": r"\b(ceo|cfo|chief executive|chief financial officer) (resigns?|steps down|departs|exits|ousted|fired)",
    "regulatory_rejection": r"complete response letter|fda (rejects|declines)|rejected by (the )?fda",
    "dividend_cut": r"dividend cut|cuts? (its )?dividend|suspends? (its )?dividend",
    "data_breach": r"data breach|cyberattack|hack(ed|ers)",
    "recall": r"\brecall(s|ed)?\b",
}

# Allegations made by others. A company release that mentions one is usually a response to it, so the
# release does not back the flag, and the company's denial does not clear it either.
ALLEGATION_FLAGS = frozenset({"fraud", "sec_charges", "short_report", "investigation", "data_breach"})
# Severe flags about the company's own actions (a filing, a restatement, an auditor leaving), which the
# company's own release can confirm.
COMPANY_SEVERE_FLAGS = frozenset(SEVERE_RED_FLAGS) - ALLEGATION_FLAGS

# A red-flag phrase does not count when its own clause negates or denies it: "denies bankruptcy report",
# "is not filing for Chapter 11", "no plans to file", "the bankruptcy rumor is false". "not", "no" and
# "never" reach the next five words, verbs such as "denies" the next nine, and neither crosses a clause
# break. Words such as "false" or "fake" do not negate what follows them ("false statements in a fraud
# scheme").
NEGATORS = r"\b(not|no|never|nor|without)\b|n't\b"
REBUTTALS = (
    r"\b(den(y|ies|ied|ying|ial)|refut(e|es|ed|ing)|reject(s|ed|ing)?|dismiss(es|ed|ing)?|"
    r"disput(e|es|ed|ing)|debunk(s|ed|ing)?|rebut(s|ted|ting)?|avoid(s|ed|ing)?|avert(s|ed|ing)?|"
    r"rul(e|es|ed|ing) out)\b"
)
NEGATED_AFTER = [  # matched right after the phrase: "bankruptcy rumors are false", "Chapter 11 report baseless"
    r"^\s*(\w+\s+){0,3}?(is|are|was|were|been)\s+(\w+\s+)?(false|untrue|not true|fake|bogus|baseless|"
    r"unfounded|groundless|a hoax)\b",
    r"^\s*(\w+\s+){0,3}?(untrue|not true|baseless|unfounded|groundless|a hoax|without merit)\b",
    r"^\s*(reports?|rumou?rs?|stor(y|ies)|claims?|news|headlines?|tweets?|posts?|(press )?releases?)\s+"
    r"(false|fake)\b",
]
CLAUSE_BREAK = (
    r"[,;:!?|()\"“”]|\s[-–—]\s|"
    # a full stop ends a sentence, but not in "Ch. 11", "ACME Corp." or "U.S."
    r"(?<!\bch)(?<!\bchap)(?<!\binc)(?<!\bcorp)(?<!\bco)(?<!\bltd)(?<!\bno)(?<!\bst)(?<!\bvs)(?<!\bu\.s)\.(\s|$)|"
    r"\b(but|as|while|amid|after|although|though|despite|yet|whereas|following)\b"
)
RELATIVE = r"\b(that|which|who|whose)\b"  # "restates results that were false" is not a denial
# A court or a regulator rejecting or dismissing something is news, not a denial by the company.
AUTHORITY_VERB = (
    r"\b(court|judge|jury|regulators?|fda|sec|ftc|doj|nasdaq|nyse|exchange|agency)\s+(\w+\s+)?"
    r"(den(y|ies|ied)|reject(s|ed)?|dismiss(es|ed)?|refus(e|es|ed))\b"
)
# Headlines that rebut a report ("denies reports", "rejects rumors"). They never confirm the story.
DENIAL_PATTERNS: list[str] = [
    r"\b(den(y|ies|ied|ying)|refut(e|es|ed|ing)|reject(s|ed|ing)?|dismiss(es|ed|ing)?|disput(e|es|ed|ing)|"
    r"debunk(s|ed|ing)?|rebut(s|ted|ting)?)\s+(\w+\s+){0,3}?(reports?|rumou?rs?|claims?|speculation|"
    r"stor(y|ies)|articles?|news|tweets?|posts?|(press )?releases?)\b",
]
# Headlines that call a piece of news fake as a whole, which also covers denials that do not repeat the
# story ("finds fake release"). Phrases such as "false reports" or "claims were false" are left out on
# purpose: they usually describe an accusation against the company, not a hoax.
_NEWS_NOUN = r"(rumou?rs?|stor(y|ies)|articles?|tweets?|posts?|(press )?releases?|headlines?|(news|media) reports?)"
HOAX_PATTERNS: list[str] = [
    r"\b(fake|bogus|phony|hoax)\s+(press\s+)?(releases?|reports?|news|stor(y|ies)|headlines?|tweets?|posts?|"
    r"announcements?|articles?)\b",
    r"\bfalse\s+(rumou?rs?|news|headlines?|tweets?|press releases?)\b",
    r"\bhoax\b",
    rf"\b{_NEWS_NOUN}\s+(\w+\s+){{0,2}}?(is|are|was|were)\s+(false|untrue|not true|fake|bogus|baseless|"
    r"unfounded|groundless|fabricated|a hoax)\b",
    rf"\b(calls?|called|labels?|brands?|branded|deems?)\s+((the|this|that|these)\s+)?(\w+\s+)?"
    rf"(reports?|news|{_NEWS_NOUN})\s+(\w+\s+)?(false|untrue|fake|bogus|baseless|unfounded|groundless|"
    r"fabricated|a hoax)\b",
    r"\b(retract(s|ed|ing)?|withdr(aw|aws|ew|awn))\s+(\w+\s+){0,2}?(stor(y|ies)|articles?|tweets?|posts?|"
    r"headlines?)\b",
]

OPINION_PATTERNS: list[str] = [
    r"\?\s*$", r"should you (buy|sell)", r"is it (time|too late) to", r"stocks? to (buy|sell|watch)",
    r"\bbest\b.*\bstocks?\b", r"\btop \d+\b", r"^\d+ (stocks|reasons|things)", r"\bbuy the dip\b",
    r"could (soar|skyrocket|double|triple|make you)", r"millionaire", r"^prediction:", r"\bwhy i\b",
    r"here's why", r"\bmy (top|favorite)\b", r"^forget\b", r"better buy", r"\bvs\.?\b", r"no-brainer",
    r"screaming buy", r"unstoppable", r"\bnever sell\b", r"i'd buy", r"\bmagnificent\b", r"\bgenius\b",
    r"before it'?s too late", r"\bright now\b", r"dividend king", r"retire",
]

EVENT_TYPES: dict[str, str] = {
    "earnings": r"\b(earnings|results|quarter(ly)?|q[1-4]|eps|revenue|sales|profit)\b",
    "guidance": r"\b(guidance|outlook|forecast)\b",
    "analyst": r"\b(upgrade|downgrade|price target|rating|initiat(es|ed) coverage|overweight|underweight|outperform)\w*",
    "mna": r"\b(acquir\w*|acquisition|merger|buyout|takeover|to buy|deal to)\b",
    "legal": r"\b(lawsuit|sued|probe|investigation|antitrust|fine[ds]?|settle\w*|court|ruling|sec|doj|ftc)\b",
    "regulatory": r"\b(fda|approval|approv\w+|clearance|license)\b",
    "product": r"\b(launch\w*|unveil\w*|release[sd]?|introduc\w+|announc\w+)\b",
    "capital": r"\b(buyback|repurchase|dividend|offering|notes|bond|debt|split)\b",
    "management": r"\b(ceo|cfo|chief executive|resign\w*|appoint\w*|steps down|board)\b",
    "macro": r"\b(fed|fomc|inflation|cpi|jobs report|payrolls|tariff\w*|rates?)\b",
}

# SEC 8-K item codes that are events by themselves.
SEC_ITEM_EVENTS: dict[str, tuple[str, float, str | None]] = {
    "1.01": ("material agreement", 0.3, None),
    "1.03": ("bankruptcy or receivership", -1.0, "bankruptcy"),
    "2.01": ("completed acquisition or disposition", 0.2, None),
    "2.02": ("earnings results", 0.0, None),
    "2.05": ("restructuring / exit costs", -0.4, None),
    "2.06": ("material impairment", -0.6, None),
    "3.01": ("delisting or listing-rule deficiency notice", -0.9, "delisting"),
    "4.01": ("change of auditor", -0.4, None),
    "4.02": ("non-reliance on previous financial statements", -1.0, "accounting"),
    "5.02": ("director or officer departure / appointment", -0.1, None),
    "8.01": ("other events", 0.0, None),
}

PRIMARY_PUBLISHERS = {"sec edgar"}
# Press-release distribution services. A release on one of them is the company speaking through its own
# channel: outlets that rewrite it do not confirm it independently, several wires carrying the same
# release are still one source, and anyone with a wire account can post a release in a company's name.
PRESS_WIRES = {
    "business wire", "pr newswire", "globenewswire", "internet wire", "accesswire", "newsfile",
    "marketwired", "prweb", "ein presswire", "canada newswire",
}

PUBLISHER_ALIASES = {
    "businesswire": "business wire", "prnewswire": "pr newswire", "pr newswire (us)": "pr newswire",
    "globe newswire": "globenewswire", "internetwire": "internet wire", "access newswire": "accesswire",
    "accessnewswire": "accesswire", "newsfile corp": "newsfile", "newsfile corp.": "newsfile",
    "newsfilecorp": "newsfile", "marketwire": "marketwired", "market wire": "marketwired", "pr web": "prweb",
    "einpresswire": "ein presswire", "einnews": "ein presswire", "cnw group": "canada newswire",
    "bloomberg television": "bloomberg", "bloomberg tv": "bloomberg", "bloomberg news": "bloomberg",
    "thomson reuters": "reuters",
    "barrons.com": "barron's", "barron's": "barron's", "barrons": "barron's",
    "wsj": "wall street journal", "the wall street journal": "wall street journal",
    "marketwatch": "marketwatch", "reuters": "reuters", "bloomberg": "bloomberg",
    "investing.com": "investing.com", "yahoo finance": "yahoo finance", "yahoo": "yahoo finance",
    "the motley fool": "motley fool", "motley fool": "motley fool", "fool.com": "motley fool",
    "zacks": "zacks", "zacks investment research": "zacks", "benzinga": "benzinga", "cnbc": "cnbc",
    "seeking alpha": "seeking alpha", "insidermonkey": "insider monkey", "insider monkey": "insider monkey",
    "tipranks": "tipranks", "gurufocus.com": "gurufocus", "gurufocus": "gurufocus",
    "simply wall st.": "simply wall st", "simply wall st": "simply wall st",
    "investor's business daily": "investor's business daily", "ibd": "investor's business daily",
    "associated press": "associated press", "ap news": "associated press",
    "financial times": "financial times", "ft.com": "financial times", "the new york times": "new york times",
    "rttnews": "rttnews", "business insider": "business insider", "fortune": "fortune",
    "forbes": "forbes", "thestreet": "thestreet", "thestreet.com": "thestreet", "24/7 wall st.": "24/7 wall st",
}

# Outlets owned by the same group do not count as independent confirmation.
PUBLISHER_FAMILY = {
    "wall street journal": "dow jones", "barron's": "dow jones", "marketwatch": "dow jones",
    "dow jones newswires": "dow jones", "investor's business daily": "ibd",
    "insider monkey": "insider monkey", "yahoo finance": "yahoo",
    **{wire: "press release" for wire in PRESS_WIRES},
}

TIER1 = {"reuters", "bloomberg", "wall street journal", "barron's", "marketwatch", "financial times", "cnbc",
         "associated press", "new york times", "dow jones newswires", "sec edgar"}
TIER2 = {"yahoo finance", "seeking alpha", "benzinga", "investing.com", "investor's business daily",
         "business insider", "fortune", "forbes", "thestreet", "the information", "axios", "techcrunch",
         "rttnews", "zacks", "tipranks", "motley fool", "nasdaq"} | PRESS_WIRES
KNOWN_MEDIA = TIER1 | TIER2 | set(PUBLISHER_ALIASES.values())

STOPWORDS = set(
    """a an the and or but of to in on for with at by from as is are was were be been being this that these
    those it its it's into over after before about than then so such via amid says said say new news stock
    stocks share shares inc corp co company corporation ltd plc group holdings today week year quarter
    update updates report reports reported market markets investors investor wall street us u.s nyse nasdaq
    what why how when where who will would could may might can his her their our your more most up down""".split()
)

_COMPILED: dict[str, re.Pattern[str]] = {}


def rx(pattern: str) -> re.Pattern[str]:
    p = _COMPILED.get(pattern)
    if p is None:
        p = re.compile(pattern, re.IGNORECASE)
        _COMPILED[pattern] = p
    return p


def normalize_publisher(name: str | None) -> str:
    if not name:
        return "unknown"
    n = name.strip().strip("()").lower()
    n = re.sub(r"^www\.", "", n)
    if n in PUBLISHER_ALIASES:
        return PUBLISHER_ALIASES[n]
    n2 = re.sub(r"\.(com|net|org|co)$", "", n)
    return PUBLISHER_ALIASES.get(n2, n2)


def publisher_family(name: str) -> str:
    return PUBLISHER_FAMILY.get(name, name)


def is_press_wire(publisher: str) -> bool:
    """``publisher`` is a normalized name (see ``normalize_publisher``)."""
    return publisher in PRESS_WIRES


_CORPORATE_SUFFIX = (r"\s+(corp(oration)?|inc(orporated)?|co|company|ltd|limited|plc|llc|group|holdings?|"
                     r"newsroom|ir|investor relations|press room)\.?$")


def is_company_statement(publisher: str | None, title: str) -> bool:
    """The publisher is the company the headline is about, e.g. "ACME Corp" posting "ACME Corp denies ..."
    on its own site. Used only to weigh denials: a statement on the company's own site can clear a story,
    but it cannot confirm bad news (that takes the 8-K or a press release)."""
    p = normalize_publisher(publisher)
    if p == "unknown" or p in KNOWN_MEDIA or p in PRIMARY_PUBLISHERS:
        return False
    core = p.rstrip(" .")
    while True:
        shorter = re.sub(_CORPORATE_SUFFIX, "", core).rstrip(" .,")
        if shorter == core:
            break
        core = shorter
    return len(core) >= 3 and re.match(re.escape(core) + r"\b", title.lower().strip()) is not None


def reliability(publisher: str) -> float:
    if publisher in TIER1:
        return 1.0
    if publisher in TIER2:
        return 0.7
    return 0.4


def sentiment(text: str) -> float:
    """Score in [-1, 1] from weighted phrase matches; phrases are consumed so they count once.

    In "X falls despite record quarter" the clause after "despite" counts a quarter as much. Negated or
    denied phrases do not count ("denies bankruptcy report"), and a headline about a fake release or a
    hoax scores 0.
    """
    if not text:
        return 0.0
    low = _prepare(text)
    if is_hoax(low):
        return 0.0
    m = re.search(r"\b(despite|even as|although|though)\b", low)
    if m:
        main, aside = low[: m.start()], low[m.end():]
        pos_m, neg_m = _raw_scores(main)
        pos_a, neg_a = _raw_scores(aside)
        pos, neg = pos_m + 0.25 * pos_a, neg_m + 0.25 * neg_a
    else:
        pos, neg = _raw_scores(low)
    total = pos + neg
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (pos - neg) / (total + 1.0)))


def _raw_scores(remaining: str) -> tuple[float, float]:
    pos = neg = 0.0
    for table, positive in ((POSITIVE, True), (NEGATIVE, False)):
        for pattern, weight in table:
            hits = list(rx(pattern).finditer(remaining))
            if not hits:
                continue
            counted = weight * sum(not _negated(remaining, m.start(), m.end()) for m in hits)
            if positive:
                pos += counted
            else:
                neg += counted
            remaining = rx(pattern).sub(" ", remaining)
    return pos, neg


def _prepare(text: str) -> str:
    return text.lower().replace("\u2019", "'")  # curly apostrophe, as in "isn’t"


def _negated(low: str, start: int, end: int) -> bool:
    """True when the phrase at ``low[start:end]`` is negated or denied within its own clause."""
    cut = 0
    for m in rx(CLAUSE_BREAK).finditer(low, 0, start):
        cut = m.end()
    before = rx(AUTHORITY_VERB).sub(" ", low[cut:start])
    before = rx(r"\bnot only\b|\bno\.?\s*\d").sub(" ", before)
    for pattern, reach in ((REBUTTALS, 8), (NEGATORS, 4)):
        for m in rx(pattern).finditer(before):
            if len(before[m.end():].split()) <= reach:
                return True
    after = low[end:]
    stop = rx(CLAUSE_BREAK + "|" + RELATIVE).search(after)
    if stop:
        after = after[: stop.start()]
    return any(rx(p).search(after) for p in NEGATED_AFTER)


def is_hoax(text: str) -> bool:
    """The headline says a release, story or post is fake ("finds fake release", "bankruptcy rumor a hoax")."""
    low = rx(AUTHORITY_VERB).sub(" ", _prepare(text))
    return any(rx(p).search(low) for p in HOAX_PATTERNS)


def _flag_hits(text: str) -> list[tuple[str, bool, bool]]:
    """(flag, severe, negated) for every red-flag phrase in the headline, severe flags first."""
    low = _prepare(text)
    hoax = is_hoax(low)
    hits = []
    for table, severe in ((SEVERE_RED_FLAGS, True), (MODERATE_RED_FLAGS, False)):
        for name, pattern in table.items():
            for m in rx(pattern).finditer(low):
                hits.append((name, severe, hoax or _negated(low, m.start(), m.end())))
    return hits


def red_flags(text: str) -> tuple[list[str], bool]:
    """Return (flags, any_severe) for the red flags the headline asserts; denied or negated ones do not count."""
    flags: list[str] = []
    severe = False
    for name, sev, negated in _flag_hits(text):
        if not negated and name not in flags:
            flags.append(name)
            severe = severe or sev
    return flags, severe


def denied_flags(text: str) -> list[str]:
    """Red flags the headline denies or negates ("not filing for Chapter 11" -> ["bankruptcy"])."""
    hits = _flag_hits(text)
    asserted = {name for name, _, negated in hits if not negated}
    out: list[str] = []
    for name, _, negated in hits:
        if negated and name not in asserted and name not in out:
            out.append(name)
    return out


def is_denial(text: str) -> bool:
    """The headline denies, rejects or retracts a report, or calls it fake. Such a headline is never
    counted as a publisher that confirms the story it denies."""
    if denied_flags(text) or is_hoax(text):
        return True
    low = rx(AUTHORITY_VERB).sub(" ", _prepare(text))
    return any(rx(p).search(low) for p in DENIAL_PATTERNS)


def is_opinion(title: str) -> bool:
    low = title.lower().strip()
    return any(rx(p).search(low) for p in OPINION_PATTERNS)


def event_types(text: str) -> list[str]:
    low = text.lower()
    return [name for name, pattern in EVENT_TYPES.items() if rx(pattern).search(low)]


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9$%.]+", text.lower())
    out = set()
    for w in words:
        w = w.strip(".")
        if len(w) < 2 or w in STOPWORDS:
            continue
        if w.endswith("s") and len(w) > 4 and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out
