"""The website: the Search Console verification must stay, and the social card must fit GitHub's limits."""

from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def test_search_console_verification_stays():
    assert (SITE / "google3ed451f05041f5f9.html").read_text(encoding="utf-8").startswith("google-site-verification:")
    assert '<meta name="google-site-verification"' in (SITE / "index.html").read_text(encoding="utf-8")


def test_social_card_fits_github_and_link_previews():
    card = SITE / "assets" / "social-preview.png"
    head = card.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", head[16:24]) == (1280, 640)
    assert card.stat().st_size <= 1_000_000, "GitHub takes social previews up to 1 MB"
    html = (SITE / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<meta property="og:image" content="[^"]+/assets/social-preview\.png">', html)


def test_every_og_image_exists():
    for page in SITE.rglob("*.html"):
        for url in re.findall(r'<meta property="og:image" content="([^"]+)">', page.read_text(encoding="utf-8")):
            name = url.rsplit("/assets/", 1)[-1]
            assert (SITE / "assets" / name).is_file(), f"{page.name}: {url}"


def test_llms_txt_lists_every_english_guide():
    """site/llms.txt is the plain-text map that AI search tools read; every English page in the sitemap is in it."""
    text = (SITE / "llms.txt").read_text(encoding="utf-8")
    assert text.startswith("# Risk-Aware Trading Agents\n\n> ")
    pages = re.findall(r"<loc>([^<]+)</loc>", (SITE / "sitemap.xml").read_text(encoding="utf-8"))
    guides = [u for u in pages if u.endswith(".html") and "/zh/" not in u]
    assert guides and all(f"]({u})" in text for u in guides)
    for name in re.findall(r"blob/main/([^)#\s]+)\)", text):
        assert (ROOT / name).is_file(), f"llms.txt links to a missing file: {name}"


def test_indexnow_key_file_matches_the_workflow():
    key = re.search(r"INDEXNOW_KEY: ([0-9a-f]{32})", (ROOT / ".github" / "workflows" / "pages.yml").read_text(
        encoding="utf-8")).group(1)
    assert (SITE / f"{key}.txt").read_text(encoding="utf-8").strip() == key
