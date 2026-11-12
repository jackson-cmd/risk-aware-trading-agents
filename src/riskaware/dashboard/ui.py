"""Look and feel of the dashboard: theme, CSS and small HTML/SVG components (cards, sparklines, gauge)."""

from __future__ import annotations

import base64
import hashlib
import html
import math
from collections.abc import Sequence

FONT = "Inter:https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"
MONO = "'JetBrains Mono':https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap"

# passed to `streamlit run` by `riskaware dashboard`
THEME_FLAGS = [
    "--theme.base", "dark",
    "--theme.primaryColor", "#5b8cff",
    "--theme.backgroundColor", "#0a0e13",
    "--theme.secondaryBackgroundColor", "#121922",
    "--theme.textColor", "#e6edf3",
    "--theme.borderColor", "#1f2a36",
    "--theme.baseRadius", "0.6rem",
    "--theme.font", f"{FONT}, sans-serif",
    "--theme.codeFont", f"{MONO}, monospace",
    "--theme.showSidebarBorder", "false",
    "--client.toolbarMode", "viewer",
]

DARK = dict(bg="#0a0e13", panel="#10161d", panel2="#151d27", line="#1f2a36", line2="#2a3644", text="#e6edf3",
            muted="#8a99a8", faint="#5b6875", accent="#5b8cff", up="#22c55e", down="#f0506e", warn="#f5a524",
            neutral="#94a3b8", violet="#a78bfa", cyan="#22d3ee")
LIGHT = dict(bg="#f7f8fa", panel="#ffffff", panel2="#f2f4f7", line="#e3e7ec", line2="#d0d7de", text="#0f172a",
             muted="#5b6675", faint="#8b95a1", accent="#3b6ef5", up="#15803d", down="#d92d50", warn="#b7791f",
             neutral="#64748b", violet="#7c3aed", cyan="#0891b2")

REGIME_KEYS = {"RISK_ON": "up", "NEUTRAL": "neutral", "UNCERTAIN": "warn", "RISK_OFF": "down"}
SERIES = ["accent", "up", "warn", "violet", "cyan", "down", "neutral"]


def palette(kind: str | None) -> dict[str, str]:
    return LIGHT if kind == "light" else DARK


def esc(x: object) -> str:
    return html.escape(str(x), quote=True)


def css(p: dict[str, str]) -> str:
    return f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');
:root {{ --bg:{p['bg']}; --panel:{p['panel']}; --panel2:{p['panel2']}; --line:{p['line']}; --line2:{p['line2']};
  --text:{p['text']}; --muted:{p['muted']}; --faint:{p['faint']}; --accent:{p['accent']}; --up:{p['up']};
  --down:{p['down']}; --warn:{p['warn']}; --neutral:{p['neutral']}; --violet:{p['violet']}; --cyan:{p['cyan']}; }}
html, body, [data-testid="stAppViewContainer"] {{ font-family: Inter, system-ui, -apple-system, sans-serif; }}
[data-testid="stAppViewContainer"] {{ background:
  radial-gradient(1200px 500px at 10% -10%, color-mix(in srgb, var(--accent) 9%, transparent), transparent 60%),
  radial-gradient(900px 400px at 100% 0%, color-mix(in srgb, var(--violet) 7%, transparent), transparent 60%),
  var(--bg); }}
[data-testid="stMainBlockContainer"], .block-container {{ max-width: 1320px; padding-top: 4.2rem; padding-bottom: 4rem; }}
[data-testid="stAppDeployButton"], [data-testid="stMainMenu"], footer {{ display: none !important; }}
[data-testid="stHeader"] {{ background: color-mix(in srgb, var(--bg) 82%, transparent); backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--line); }}
h1, h2, h3, h4 {{ letter-spacing: -0.02em; }}
.mono, .num {{ font-family: 'JetBrains Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }}
.ra-brand {{ display:flex; align-items:center; gap:14px; margin: 0 0 6px; }}
.ra-logo {{ width:40px; height:40px; border-radius:12px; display:grid; place-items:center;
  background: linear-gradient(135deg, var(--accent), var(--violet)); box-shadow: 0 6px 24px color-mix(in srgb, var(--accent) 35%, transparent); }}
.ra-brand h1 {{ font-size: 1.45rem; margin:0; padding:0; font-weight:800; }}
.ra-brand .sub {{ color: var(--muted); font-size: .86rem; margin-top:2px; }}
.ra-section {{ display:flex; align-items:baseline; justify-content:space-between; margin: 26px 0 10px; }}
.ra-section h3 {{ font-size: 1.02rem; font-weight: 700; margin:0; padding:0; }}
.ra-section .hint {{ color: var(--muted); font-size: .82rem; }}
.ra-card {{ background: linear-gradient(180deg, color-mix(in srgb, var(--panel2) 70%, transparent), var(--panel));
  border:1px solid var(--line); border-radius:16px; padding:16px 18px; position:relative; overflow:hidden; }}
.ra-card:hover {{ border-color: var(--line2); }}
.ra-grid {{ display:grid; gap:12px; }}
.ra-g6 {{ grid-template-columns: repeat(6, minmax(0,1fr)); }}
.ra-g4 {{ grid-template-columns: repeat(4, minmax(0,1fr)); }}
.ra-g3 {{ grid-template-columns: repeat(3, minmax(0,1fr)); }}
@media (max-width: 1100px) {{ .ra-g6 {{ grid-template-columns: repeat(3, minmax(0,1fr)); }} .ra-g4 {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} }}
@media (max-width: 760px) {{ .ra-g6, .ra-g4, .ra-g3 {{ grid-template-columns: repeat(2, minmax(0,1fr)); }} .ra-hero {{ grid-template-columns: 1fr !important; }} }}
.ra-kpi .label {{ color: var(--muted); font-size:.74rem; font-weight:600; text-transform:uppercase; letter-spacing:.06em; }}
.ra-kpi .value {{ font-family:'JetBrains Mono',monospace; font-size:1.55rem; font-weight:600; margin-top:6px; line-height:1.1; }}
.ra-kpi .foot {{ display:flex; justify-content:space-between; align-items:flex-end; margin-top:8px; gap:6px; }}
.ra-kpi .sub {{ font-size:.78rem; color: var(--muted); }}
.ra-pill {{ display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:999px; font-size:.75rem;
  font-weight:600; border:1px solid color-mix(in srgb, var(--c) 45%, transparent);
  background: color-mix(in srgb, var(--c) 14%, transparent); color: var(--c); white-space:nowrap; }}
.ra-dot {{ width:8px; height:8px; border-radius:50%; background: var(--c); box-shadow: 0 0 0 3px color-mix(in srgb, var(--c) 25%, transparent); }}
.ra-hero {{ display:grid; grid-template-columns: 1.65fr 1fr; gap:12px; }}
.ra-regime {{ padding: 22px 24px; }}
.ra-regime:before {{ content:""; position:absolute; inset:0; background: radial-gradient(600px 220px at 0% 0%, color-mix(in srgb, var(--c) 22%, transparent), transparent 70%); pointer-events:none; }}
.ra-regime .eyebrow {{ color: var(--muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.08em; font-weight:600; }}
.ra-regime .big {{ font-size: 2.3rem; font-weight:800; letter-spacing:-0.03em; color: var(--c); margin: 6px 0 4px; display:flex; align-items:center; gap:14px; }}
.ra-regime .what {{ font-size: 1rem; color: var(--text); opacity:.92; }}
.ra-chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:14px; }}
.ra-chip {{ font-size:.76rem; padding:4px 9px; border-radius:8px; background: var(--panel2); border:1px solid var(--line); color: var(--muted); }}
.ra-gauge {{ display:flex; flex-direction:column; align-items:center; justify-content:center; }}
.ra-gauge .cap {{ color: var(--muted); font-size:.78rem; margin-top:10px; }}
.ra-events {{ display:flex; gap:10px; overflow-x:auto; padding-bottom:6px; scrollbar-width: thin; }}
.ra-event {{ min-width: 168px; display:flex; gap:12px; align-items:center; padding:12px 14px; }}
.ra-event .date {{ text-align:center; min-width:42px; border-right:1px solid var(--line); padding-right:12px; }}
.ra-event .mon {{ font-size:.66rem; color: var(--muted); font-weight:700; letter-spacing:.08em; }}
.ra-event .day {{ font-family:'JetBrains Mono',monospace; font-size:1.35rem; font-weight:700; line-height:1.1; }}
.ra-event .name {{ font-size:.84rem; font-weight:600; }}
.ra-event .in {{ font-size:.74rem; color: var(--muted); margin-top:2px; }}
.ra-event .bar {{ position:absolute; left:0; top:0; bottom:0; width:3px; background: var(--c); }}
.ra-agent .top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }}
.ra-agent .rank {{ font-family:'JetBrains Mono',monospace; font-size:.78rem; color: var(--faint); font-weight:700; }}
.ra-agent .name {{ font-size:1.08rem; font-weight:700; margin-top:2px; }}
.ra-agent .meta {{ color: var(--muted); font-size:.78rem; margin-top:2px; }}
.ra-agent .eq {{ font-family:'JetBrains Mono',monospace; font-size:1.5rem; font-weight:600; margin-top:12px; }}
.ra-agent .ret {{ font-family:'JetBrains Mono',monospace; font-size:.92rem; font-weight:600; }}
.ra-agent .stats {{ display:grid; grid-template-columns: repeat(4, 1fr); gap:6px; margin-top:12px; border-top:1px solid var(--line); padding-top:10px; }}
.ra-agent .stats div span {{ display:block; color: var(--faint); font-size:.66rem; text-transform:uppercase; letter-spacing:.06em; font-weight:600; }}
.ra-agent .stats div b {{ font-family:'JetBrains Mono',monospace; font-size:.86rem; font-weight:600; }}
.ra-agent.first {{ border-color: color-mix(in srgb, var(--warn) 45%, var(--line)); }}
.ra-empty {{ text-align:center; padding: 38px 20px; }}
.ra-empty .t {{ font-size:1.15rem; font-weight:700; margin-top:10px; }}
.ra-empty .d {{ color: var(--muted); margin-top:6px; }}
.up {{ color: var(--up); }} .down {{ color: var(--down); }} .muted {{ color: var(--muted); }}
.ra-note {{ color: var(--muted); font-size:.8rem; }}
.ra-code {{ font-family:'JetBrains Mono',monospace; font-size:.8rem; background: var(--panel2); border:1px solid var(--line);
  border-radius:8px; padding:8px 10px; color: var(--text); overflow-x:auto; white-space:nowrap; }}
[data-testid="stTabs"] button[role="tab"] p {{ font-weight:600; }}
[data-testid="stExpander"] details {{ border-radius:14px; border-color: var(--line); background: var(--panel); }}
[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius:16px; }}
div[data-testid="stForm"] {{ border-radius:16px; border-color: var(--line); background: var(--panel); }}
</style>"""


# ------------------------------------------------------------------------------------- formatting
def money(x: float | None, digits: int = 0) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"${x:,.{digits}f}"


def pct(x: float | None, digits: int = 1, sign: bool = False) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    v = round(x * 100, digits)
    v = 0.0 if v == 0 else v  # no "-0.0%"
    return f"{v:{'+' if sign and v else ''}.{digits}f}%"


def num(x: float | None, digits: int = 1) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "–"
    return f"{x:,.{digits}f}"


def tone(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)) or abs(x) < 1e-12:
        return "muted"
    return "up" if x > 0 else "down"


# ------------------------------------------------------------------------------------- components
def as_img(svg: str, width: float, height: float, alt: str = "", stretch: bool = False) -> str:
    """st.html strips inline SVG, so charts go in as data-URI images."""
    if 'xmlns=' not in svg:
        svg = svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    size = (f'style="display:block;width:100%;height:{height:.0f}px"' if stretch
            else f'width="{width:.0f}" height="{height:.0f}" style="display:block"')
    return f'<img src="data:image/svg+xml;base64,{data}" {size} alt="{esc(alt)}">'


def sparkline(values: Sequence[float], p: dict[str, str], width: int = 120, height: int = 34,
              color: str | None = None, fill: bool = True, stretch: bool = False) -> str:
    vals = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    pts = [(i * (width - 2) / (n - 1) + 1, height - 3 - (v - lo) / span * (height - 6)) for i, v in enumerate(vals)]
    c = color or (p["up"] if vals[-1] >= vals[0] else p["down"])
    gid = "g" + hashlib.md5(f"{vals[:3]}{vals[-3:]}{n}{c}".encode()).hexdigest()[:8]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"M{pts[0][0]:.1f},{height} L" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" L{pts[-1][0]:.1f},{height} Z"
    par = ' preserveAspectRatio="none"' if stretch else ""
    return as_img(f'<svg width="{width}" height="{height}" viewBox="-2 -2 {width + 4} {height + 4}"{par}>'
            f'<defs><linearGradient id="{gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{c}" '
            f'stop-opacity=".35"/><stop offset="1" stop-color="{c}" stop-opacity="0"/></linearGradient></defs>'
            + (f'<path d="{area}" fill="url(#{gid})"/>' if fill else "")
            + f'<polyline points="{line}" fill="none" stroke="{c}" stroke-width="1.8" stroke-linejoin="round" '
              f'stroke-linecap="round"/><circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="2.6" fill="{c}"/></svg>',
                  width, height, stretch=stretch)


def kpi(label: str, value: str, sub: str = "", spark: str = "", sub_class: str = "muted", help_text: str = "") -> str:
    title = f' title="{esc(help_text)}"' if help_text else ""
    return (f'<div class="ra-card ra-kpi"{title}><div class="label">{esc(label)}</div><div class="value">{value}</div>'
            f'<div class="sub {sub_class}" style="margin-top:6px">{sub}</div>'
            + (f'<div style="margin-top:8px">{spark}</div>' if spark else "") + "</div>")


def pill(text: str, color: str, dot: bool = True) -> str:
    return (f'<span class="ra-pill" style="--c:{color}">' + ('<span class="ra-dot"></span>' if dot else "")
            + f"{esc(text)}</span>")


def regime_color(regime: str, p: dict[str, str]) -> str:
    return p[REGIME_KEYS.get(regime, "neutral")]


def gauge(value: float | None, p: dict[str, str], label: str = "", size: int = 240) -> str:
    """Semicircle gauge from 0 (extreme fear) to 100 (extreme greed) with a marker on the arc."""
    w, h = size, size * 0.60
    cx, cy, r = w / 2, h - 6, size * 0.40
    bands = [(0, 25, p["down"]), (25, 45, p["warn"]), (45, 55, p["neutral"]), (55, 75, "#7ed69b"), (75, 100, p["up"])]

    def pt(v: float, rad: float) -> tuple[float, float]:
        a = math.pi * (1 - v / 100)
        return cx + rad * math.cos(a), cy - rad * math.sin(a)

    arcs = []
    for a, b, c in bands:
        x1, y1 = pt(a + 0.8, r)
        x2, y2 = pt(b - 0.8, r)
        arcs.append(f'<path d="M{x1:.1f},{y1:.1f} A{r},{r} 0 0 1 {x2:.1f},{y2:.1f}" stroke="{c}" stroke-width="12" '
                    f'fill="none" stroke-linecap="round" opacity=".85"/>')
    marker, txt, col = "", "–", p["muted"]
    if value is not None and not math.isnan(value):
        v = max(0.0, min(100.0, value))
        col = next(c for a, b, c in bands if v <= b)
        mx, my = pt(v, r)
        marker = (f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="11" fill="{p["bg"]}" stroke="{p["text"]}" '
                  f'stroke-width="3"/><circle cx="{mx:.1f}" cy="{my:.1f}" r="5" fill="{col}"/>')
        txt = f"{v:.0f}"
    return as_img(f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(arcs)}{marker}'
                  f'<text x="{cx}" y="{cy - r * 0.30}" text-anchor="middle" font-family="Menlo, Consolas, monospace" '
                  f'font-size="{size * 0.17:.0f}" font-weight="700" fill="{p["text"]}">{txt}</text>'
                  f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" font-size="{size * 0.06:.0f}" font-weight="700" '
                  f'fill="{col}" font-family="Inter, Helvetica, Arial, sans-serif">{esc(label.upper())}</text></svg>',
                  w, h, label)


def section(title: str, hint: str = "") -> str:
    return f'<div class="ra-section"><h3>{esc(title)}</h3><div class="hint">{esc(hint)}</div></div>'


def brand(title: str, sub: str, right: str = "") -> str:
    logo = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2.4" '
            'stroke-linecap="round" stroke-linejoin="round"><polyline points="3 17 9 11 13 15 21 7"/>'
            '<polyline points="15 7 21 7 21 13"/></svg>')
    return (f'<div class="ra-brand" style="justify-content:space-between"><div style="display:flex;gap:14px;'
            f'align-items:center"><div class="ra-logo">{logo}</div><div><h1>{esc(title)}</h1><div class="sub">'
            f'{esc(sub)}</div></div></div><div>{right}</div></div>')


def empty(icon: str, title: str, desc: str) -> str:
    return (f'<div class="ra-card ra-empty"><div style="font-size:2.2rem">{icon}</div><div class="t">{esc(title)}</div>'
            f'<div class="d">{esc(desc)}</div></div>')


def plotly_layout(p: dict[str, str], height: int = 320, title: str | None = None, **kw) -> dict:
    lay = dict(template="plotly_dark" if p is DARK else "plotly_white",
               height=height, margin=dict(l=8, r=8, t=44 if title else 8, b=8), paper_bgcolor="rgba(0,0,0,0)",
               plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, sans-serif", color=p["muted"], size=12),
               colorway=[p[k] for k in SERIES], hovermode="x unified",
               legend=dict(orientation="h", y=1.02, yanchor="bottom", x=1, xanchor="right", bgcolor="rgba(0,0,0,0)"),
               xaxis=dict(gridcolor=p["line"], zeroline=False, linecolor=p["line"], showspikes=False),
               yaxis=dict(gridcolor=p["line"], zeroline=False, linecolor=p["line"]),
               hoverlabel=dict(bgcolor=p["panel2"], bordercolor=p["line2"], font=dict(color=p["text"])))
    if title:
        lay["title"] = dict(text=title, font=dict(size=14, color=p["text"]), x=0, xanchor="left")
    lay.update(kw)
    return lay
