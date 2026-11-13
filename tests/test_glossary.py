"""The dashboard's ? popups: every key the pages use exists, in both languages, and renders safely."""

import re
from pathlib import Path

from riskaware.backtest.metrics import METRIC_LABELS
from riskaware.dashboard import glossary as gl
from riskaware.dashboard import ui

APP = Path(gl.__file__).with_name("app.py").read_text(encoding="utf-8")


def test_every_key_the_pages_use_exists_in_both_languages():
    used = set(re.findall(r'\bH?Q\("([a-z_]+)"\)', APP)) | set(re.findall(r'"(technical|earnings|news_part|'
                                                                         r'macro_part)"\)', APP))
    assert len(used) >= 40
    missing = sorted(k for k in used if k not in gl.GLOSSARY)
    assert not missing, missing
    for key in gl.GLOSSARY:
        for lang in ("en", "zh"):
            e = gl.entry(key, lang)
            assert all(e[f].strip() for f in ("title", "what", "how", "use")), (key, lang)
    assert gl.entry("nope") is None and gl.help_md("nope") == ""


def test_every_backtest_metric_has_a_definition():
    assert set(METRIC_LABELS) <= set(gl.METRICS)
    assert all(gl.metric_help(k, "zh") and gl.metric_help(k, "en") for k in METRIC_LABELS)


def test_the_popup_markup():
    html = ui.tip({"title": "A <b>", "what": "first line\n- one `x`\n- two", "how": "h", "use": ""})
    pid = re.search(r'popovertarget="(ra-tip-\d+)"', html).group(1)
    assert f'id="{pid}"' in html and 'popover="auto"' in html and html.count(f'popovertarget="{pid}"') == 2
    assert "A &lt;b&gt;" in html and "<ul><li>one <code>x</code></li><li>two</li></ul>" in html
    assert "How to use it" not in html  # empty sections are left out
    assert ui.tip(None) == "" and ui.tip({"title": "t", "what": "w"}) != ui.tip({"title": "t", "what": "w"})


def test_help_markdown_keeps_lists_apart_from_paragraphs():
    md = gl.help_md("fear_greed", "en")
    assert md.startswith("**Fear & greed (computed)**") and "**What it is**: " in md
    assert "\n\n- momentum" in md and "\n- breadth" in md and "\n\nLabels:" in md
    assert "**是什么**：" in gl.help_md("vix", "zh")
