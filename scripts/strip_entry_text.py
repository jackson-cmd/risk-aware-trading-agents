"""Rewrite leaderboard entries so that they publish the models' orders only.

    python scripts/strip_entry_text.py leaderboard/entries/*.json

Each reply keeps its orders (symbol, action, weight, fraction); an order's reason and a reply's note are dropped,
because they are the model's own words and can echo the licensed headlines it read. ``record.replies_sha256`` is
recomputed. The orders, and so every number a replay gives, stay the same. Files that are already clean are left
alone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from riskaware.research.bench import _json_default, _sha, public_replies


def strip(path: Path) -> bool:
    """Rewrite one entry in place; True when it changed."""
    entry = json.loads(path.read_text(encoding="utf-8"))
    replies = public_replies(entry["replies"])
    if replies == entry["replies"]:
        return False
    entry["replies"] = replies
    entry["record"]["replies_sha256"] = _sha(replies)
    path.write_text(json.dumps(entry, indent=1, ensure_ascii=False, default=_json_default) + "\n", encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("entries", nargs="+", type=Path)
    a = ap.parse_args()
    changed = [p.name for p in a.entries if strip(p)]
    print(f"{len(changed)} of {len(a.entries)} entries rewritten" + (": " + ", ".join(changed) if changed else ""))


if __name__ == "__main__":
    main()
