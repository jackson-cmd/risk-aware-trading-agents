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
