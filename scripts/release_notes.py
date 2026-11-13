"""Release notes for one version: its section of CHANGELOG.md, with links that work on the release page.

    python scripts/release_notes.py 0.7.0      # the notes for v0.7.0
    python scripts/release_notes.py --version  # the version in pyproject.toml

The release workflow (.github/workflows/release.yml) runs it when a new version reaches main. Relative links in
the changelog point into the repository; on a release page they would break, so they become links to the files as
they were at that version's tag.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("GITHUB_REPOSITORY", "jackson-cmd/risk-aware-trading-agents")


def project_version(root: Path = ROOT) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("pyproject.toml has no version")
    return m.group(1)


def changelog_section(version: str, root: Path = ROOT) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group(1).strip()


def absolute_links(text: str, tag: str, repo: str = REPO) -> str:
    def fix(m: re.Match) -> str:
        target = m.group(2)
        if re.match(r"^(https?:|mailto:|#)", target):
            return m.group(0)
        path = target[2:] if target.startswith("./") else target
        return f"{m.group(1)}(https://github.com/{repo}/blob/{tag}/{path})"

    return re.sub(r"(\[[^\]]*\])\(([^)\s]+)\)", fix, text)


def previous_version(version: str, root: Path = ROOT) -> str | None:
    """The version of the changelog section right below this one."""
    heads = re.findall(r"^## (\d+\.\d+\.\d+)\b", (root / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    i = heads.index(version) if version in heads else -1
    return heads[i + 1] if 0 <= i < len(heads) - 1 else None


def notes(version: str, root: Path = ROOT, repo: str = REPO) -> str:
    tag = f"v{version}"
    body = absolute_links(changelog_section(version, root), tag, repo)
    prev = previous_version(version, root)
    if prev:
        body += f"\n\n**Full changelog**: https://github.com/{repo}/compare/v{prev}...{tag}"
    return (f"{body}\n\n"
            f"Install this version: `pip install risk-aware-trading-agents=={version}` · Docker: "
            f"`docker pull ghcr.io/{repo}:{version}`\n\n"
            f"中文更新记录：[docs/zh/CHANGELOG.md](https://github.com/{repo}/blob/{tag}/docs/zh/CHANGELOG.md)\n")


def main(argv: list[str]) -> None:
    if argv[1:2] == ["--version"]:
        print(project_version())
        return
    print(notes(argv[1] if len(argv) > 1 else project_version()), end="")


if __name__ == "__main__":
    main(sys.argv)
