"""Fail when a built package holds a research headline file (news_*.jsonl.gz).

    python scripts/check_dist.py dist

The headline files of the research periods are licensed and are not distributed: the wheel and the source archive
must never carry them, even when they are built from a checkout that keeps a private copy (see
src/riskaware/news/files.py). Checks every .whl and .tar.gz in the folder.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import tarfile
import zipfile
from pathlib import Path

PATTERN = "news_*.jsonl.gz"


def headline_files(path: Path) -> list[str]:
    """The headline files inside one wheel or source archive."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    else:
        with tarfile.open(path) as t:
            names = t.getnames()
    return [n for n in names if fnmatch.fnmatch(n.rsplit("/", 1)[-1], PATTERN)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder", type=Path, nargs="?", default=Path("dist"))
    a = ap.parse_args()
    packages = sorted([*a.folder.glob("*.whl"), *a.folder.glob("*.tar.gz")])
    if not packages:
        print(f"no .whl or .tar.gz in {a.folder}", file=sys.stderr)
        return 2
    bad = {p.name: found for p in packages if (found := headline_files(p))}
    for name, found in bad.items():
        print(f"{name}: {', '.join(found)}", file=sys.stderr)
    if bad:
        print("headline files must not be in a package (they are licensed)", file=sys.stderr)
        return 1
    print(f"no headline file in {', '.join(p.name for p in packages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
