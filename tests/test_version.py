"""A release takes the version from pyproject.toml; everything that shows the version has to agree with it."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import riskaware

ROOT = Path(__file__).resolve().parent.parent


def _release_notes():
    spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts" / "release_notes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_version_agrees_everywhere():
    rn = _release_notes()
    version = rn.project_version(ROOT)
    assert riskaware.__version__ == version
    assert re.search(rf"^## {re.escape(version)}\b", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert re.search(rf"^## {re.escape(version)}\b", (ROOT / "docs/zh/CHANGELOG.md").read_text(encoding="utf-8"),
                     re.M)
    for readme in ("README.md", "README_zh.md"):
        assert f"-{version}-e5534b" in (ROOT / readme).read_text(encoding="utf-8"), f"{readme} version badge"


def test_release_notes_link_to_the_tag():
    rn = _release_notes()
    version = rn.project_version(ROOT)
    text = rn.notes(version, ROOT, "owner/repo")
    tag = f"v{version}"
    assert rn.changelog_section(version, ROOT).splitlines()[0][:40] in text
    links = re.findall(r"\]\(([^)\s]+)\)", text)
    assert links and all(re.match(r"^(https?:|#)", u) for u in links), "every link is absolute"
    assert f"https://github.com/owner/repo/blob/{tag}/" in text
    assert f"@{tag}" in text
    assert rn.absolute_links("[a](docs/x.md) [b](#y) [c](https://z) [d](.github/C.md)", "v1", "o/r") == \
        ("[a](https://github.com/o/r/blob/v1/docs/x.md) [b](#y) [c](https://z) "
         "[d](https://github.com/o/r/blob/v1/.github/C.md)")
