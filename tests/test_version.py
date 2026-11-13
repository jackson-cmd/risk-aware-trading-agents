"""A release takes the version from pyproject.toml; everything that shows the version has to agree with it."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

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
    for log in ("CHANGELOG.md", "docs/zh/CHANGELOG.md"):
        newest = re.search(r"^## (\d+\.\d+\.\d+)", (ROOT / log).read_text(encoding="utf-8"), re.M)
        assert newest and newest.group(1) == version, f"{log}: the newest section is not {version}"
    assert f'version: "{version}"' in (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    for readme in ("README.md", "README_zh.md"):
        text = (ROOT / readme).read_text(encoding="utf-8")
        assert f"-{version}-e5534b" in text, f"{readme} version badge"
        assert f"version = {{{version}}}" in text, f"{readme} BibTeX version"


def test_release_notes_link_to_the_tag():
    rn = _release_notes()
    version = rn.project_version(ROOT)
    text = rn.notes(version, ROOT, "owner/repo")
    tag = f"v{version}"
    assert rn.changelog_section(version, ROOT).splitlines()[0][:40] in text
    links = re.findall(r"\]\(([^)\s]+)\)", text)
    assert links and all(re.match(r"^(https?:|#)", u) for u in links), "every link is absolute"
    assert f"https://github.com/owner/repo/blob/{tag}/" in text
    assert f"pip install risk-aware-trading-agents=={version}" in text
    assert f"docker pull ghcr.io/owner/repo:{version}" in text
    prev = rn.previous_version(version, ROOT)
    assert prev and f"https://github.com/owner/repo/compare/v{prev}...{tag}" in text
    assert rn.absolute_links("[a](docs/x.md) [b](#y) [c](https://z) [d](.github/C.md)", "v1", "o/r") == \
        ("[a](https://github.com/o/r/blob/v1/docs/x.md) [b](#y) [c](https://z) "
         "[d](https://github.com/o/r/blob/v1/.github/C.md)")


def test_the_pypi_readme_has_only_absolute_links():
    """PyPI shows README.md on its own; the build rewrites its relative links and images (pyproject.toml)."""
    tomllib = pytest.importorskip("tomllib")
    hook = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["metadata"]["hooks"][
        "fancy-pypi-readme"]
    text = "".join((ROOT / f["path"]).read_text(encoding="utf-8") for f in hook["fragments"])
    for s in hook["substitutions"]:
        text = re.sub(s["pattern"], s["replacement"], text)
    assert not re.findall(r"\]\((?!https?:|mailto:)[^)\s]+\)", text)
    assert not re.findall(r'(?:src|href)="(?!https?:|mailto:)[^"]*"', text)
    assert "https://raw.githubusercontent.com/jackson-cmd/risk-aware-trading-agents/main/site/assets/" in text


def test_the_mcp_registry_entry_agrees():
    """The release publishes server.json to the MCP Registry, which accepts it only if the PyPI README names the server
    and the image carries its label; `uvx risk-aware-trading-agents mcp` needs a command under the package name and the
    MCP SDK in the base install."""
    import json

    tomllib = pytest.importorskip("tomllib")
    version = _release_notes().project_version(ROOT)
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert server["version"] == version
    assert len(server["description"]) <= 100, "the registry rejects longer descriptions"
    pypi = next(p for p in server["packages"] if p["registryType"] == "pypi")
    oci = next(p for p in server["packages"] if p["registryType"] == "oci")
    assert (pypi["identifier"], pypi["version"]) == ("risk-aware-trading-agents", version)
    assert oci["identifier"] == f"ghcr.io/jackson-cmd/risk-aware-trading-agents:{version}"
    assert all(p["packageArguments"] == [{"type": "positional", "value": "mcp"}] for p in (pypi, oci))
    name = server["name"]
    assert name == "io.github.jackson-cmd/risk-aware-trading-agents"
    assert f"<!-- mcp-name: {name} -->" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert f'LABEL io.modelcontextprotocol.server.name="{name}"' in (ROOT / "docker" / "Dockerfile").read_text(
        encoding="utf-8")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["scripts"]["risk-aware-trading-agents"] == project["scripts"]["riskaware"]
    assert any(re.match(r"mcp\b", dep) for dep in project["dependencies"])
