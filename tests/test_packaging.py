"""The research headline files are licensed: never tracked by git, never inside a built package."""

from __future__ import annotations

import fnmatch
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATTERN = "news_*.jsonl.gz"
sys.path.insert(0, str(ROOT / "scripts"))
from check_dist import headline_files  # noqa: E402


def test_no_headline_file_is_tracked():
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    out = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert [f for f in out.split("\0") if fnmatch.fnmatch(f.rsplit("/", 1)[-1], PATTERN)] == []


def test_headline_files_are_ignored():
    assert PATTERN in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"**/{PATTERN}" in (ROOT / "docker" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()


def _project_copy(dest: Path) -> Path:
    """The files a build reads, with a headline file planted where versions up to 0.7.2 kept them."""
    dest.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "README_zh.md", "LICENSE", ".gitignore"):
        shutil.copy2(ROOT / name, dest / name)
    shutil.copytree(ROOT / "src", dest / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    data = dest / "src" / "riskaware" / "news" / "data"
    data.mkdir(parents=True, exist_ok=True)
    for name in ("news_2026.jsonl.gz", "news_2099.jsonl.gz"):
        (data / name).write_bytes(b"not a real headline file")
    (dest / "src" / "riskaware" / "news_extra.jsonl.gz").write_bytes(b"neither is this")
    return dest


@pytest.mark.skipif(importlib.util.find_spec("hatchling") is None
                    or importlib.util.find_spec("hatch_fancy_pypi_readme") is None,
                    reason="needs the dev extra (hatchling and hatch-fancy-pypi-readme)")
def test_built_packages_hold_no_headline_file(tmp_path):
    from hatchling.builders.sdist import SdistBuilder
    from hatchling.builders.wheel import WheelBuilder

    project = _project_copy(tmp_path / "project")
    out = tmp_path / "dist"
    wheel = Path(next(WheelBuilder(str(project)).build(directory=str(out), versions=["standard"])))
    sdist = Path(next(SdistBuilder(str(project)).build(directory=str(out), versions=["standard"])))
    assert headline_files(wheel) == [] and headline_files(sdist) == []
    import zipfile

    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
    assert "riskaware/research/data/market_2026.csv.gz" in names  # the prices still ship
    assert "riskaware/news/files.py" in names


def test_check_dist_finds_a_headline_file(tmp_path):
    import zipfile

    bad = tmp_path / "bad-0.0.0-py3-none-any.whl"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("riskaware/news/data/news_2026.jsonl.gz", b"x")
        z.writestr("riskaware/__init__.py", b"")
    assert headline_files(bad) == ["riskaware/news/data/news_2026.jsonl.gz"]
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_dist.py"), str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 1 and "news_2026.jsonl.gz" in result.stderr
