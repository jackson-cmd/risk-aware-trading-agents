"""Where the headline files of the research periods are found.

The research periods (``riskaware.research.periods``) were built with one headline file each:
``news_2020.jsonl.gz``, ``news_2022.jsonl.gz`` and ``news_2026.jsonl.gz`` (licensed headlines, titles only;
for 2026 also SEC 8-K filings). The headlines are licensed, so the files are not distributed with the package. The
published results were made with them, and each period's manifest keeps their SHA-256, so anyone who has the same
files can check that they hold the same data.

A headline file is looked for in these folders, in this order:

1. ``$RISKAWARE_NEWS_DIR``;
2. ``~/.riskaware/data/news`` (``$RISKAWARE_HOME/data/news``);
3. the package's own ``riskaware/news/data`` (versions up to 0.7.2 shipped the files there; it is still read, so
   an older install keeps working).

Without a file, an experiment with news runs without headlines and says so in its notes and manifest. To build a
headline file under the same rules: the free SEC 8-K filings (``riskaware news-history build``), a news API key of
your own or any CSV (``riskaware news-history import``), then ``scripts/build_news_bundle.py``; see
docs/news-history.md.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import RISKAWARE_HOME

#: Environment variable that names a folder holding the headline files (searched first).
NEWS_DIR_ENV = "RISKAWARE_NEWS_DIR"
#: The private folder for the headline files on your own disk.
PRIVATE_NEWS_DIR = RISKAWARE_HOME / "data" / "news"
#: The package's own folder: versions up to 0.7.2 shipped the headline files here.
PACKAGE_NEWS_DIR = Path(__file__).resolve().with_name("data")
#: The headline files of the research periods, oldest first.
PERIOD_FILES = ("news_2020.jsonl.gz", "news_2022.jsonl.gz", "news_2026.jsonl.gz")

NOT_DISTRIBUTED = ("the headlines are licensed and are not distributed with the package; put your copy of {file} "
                   "in ~/.riskaware/data/news or in the folder named by RISKAWARE_NEWS_DIR, or build your own "
                   "(docs/news-history.md)")
NOT_DISTRIBUTED_ZH = ("新闻标题有许可限制，不随包分发；把你自己的 {file} 放进 ~/.riskaware/data/news 或 "
                      "RISKAWARE_NEWS_DIR 指定的文件夹，或者自己建一份（docs/zh/news-history.md）")


def news_dirs() -> list[Path]:
    """The folders searched for a headline file, in order (read when called, so a changed variable counts)."""
    dirs = []
    env = os.environ.get(NEWS_DIR_ENV, "").strip()
    if env:
        dirs.append(Path(env).expanduser())
    dirs += [PRIVATE_NEWS_DIR, PACKAGE_NEWS_DIR]
    return dirs


def find_news_file(name: str) -> Path | None:
    """The first copy of a headline file (``"news_2026.jsonl.gz"``) in ``news_dirs()``, or None."""
    for folder in news_dirs():
        path = folder / name
        if path.is_file():
            return path
    return None


def news_file(name: str) -> Path:
    """The copy of a headline file that would be read; if there is none, the place to put it (the first folder)."""
    return find_news_file(name) or news_dirs()[0] / name


def where(path: Path | None) -> str | None:
    """Which of the folders a copy came from, without the local path: what a record may say about it."""
    if path is None:
        return None
    folder = Path(path).parent
    env = os.environ.get(NEWS_DIR_ENV, "").strip()
    if env and folder == Path(env).expanduser():
        return NEWS_DIR_ENV
    if folder == PRIVATE_NEWS_DIR:
        return "$RISKAWARE_HOME/data/news" if os.environ.get("RISKAWARE_HOME") else "~/.riskaware/data/news"
    if folder.resolve() == PACKAGE_NEWS_DIR:
        return "riskaware/news/data (package folder)"
    return "other"


def missing_message(name: str, zh: bool = False) -> str:
    """Why a headline file is missing and what to do, in English or Chinese."""
    return (NOT_DISTRIBUTED_ZH if zh else NOT_DISTRIBUTED).format(file=name)


__all__ = ["NEWS_DIR_ENV", "PACKAGE_NEWS_DIR", "PERIOD_FILES", "PRIVATE_NEWS_DIR", "find_news_file",
           "missing_message", "news_dirs", "news_file", "where"]
