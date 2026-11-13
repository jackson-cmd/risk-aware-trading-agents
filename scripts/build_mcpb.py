#!/usr/bin/env python3
"""Build the Claude Desktop extension (.mcpb) that the release attaches.

The bundle holds no copy of the code. Its pyproject.toml asks for risk-aware-trading-agents==VERSION, and Claude
Desktop installs that from PyPI with uv when the extension is opened (MCPB manifest 0.4, server type "uv"). The tool
list in the manifest is read from src/riskaware/mcp_server.py without importing it, so building needs nothing but
Python.

    python scripts/build_mcpb.py                      # dist-mcpb/risk-aware-trading-agents-<version>.mcpb
    python scripts/build_mcpb.py --wheel dist/x.whl   # depend on a local wheel instead, to try it before a release
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = "risk-aware-trading-agents"
REPO = "https://github.com/jackson-cmd/risk-aware-trading-agents"
SITE = "https://jackson-cmd.github.io/risk-aware-trading-agents/"
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # the same files give the same archive, so the hash only changes with the content

SERVER_PY = '''"""Start the Risk-Aware Trading Agents MCP server over stdio. Claude Desktop runs this file with uv."""

from riskaware.mcp_server import main

if __name__ == "__main__":
    main()
'''


def project_version(root: Path = ROOT) -> str:
    match = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit("pyproject.toml has no version")
    return match.group(1)


def tools(root: Path = ROOT) -> list[dict[str, str]]:
    """Name and first paragraph of the docstring of every function in mcp_server.TOOLS, in that order."""
    tree = ast.parse((root / "src" / "riskaware" / "mcp_server.py").read_text(encoding="utf-8"))
    docs = {node.name: ast.get_docstring(node) or "" for node in tree.body if isinstance(node, ast.FunctionDef)}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets):
            names = [element.id for element in node.value.elts]
            return [{"name": n, "description": " ".join(docs[n].split("\n\n")[0].split())} for n in names]
    raise SystemExit("mcp_server.py has no TOOLS list")


def manifest(version: str, tool_list: list[dict[str, str]]) -> dict:
    return {
        "manifest_version": "0.4",
        "name": DIST,
        "display_name": "Risk-Aware Trading Agents",
        "version": version,
        "description": "AI trading agents for US stocks: market regime, signals, backtests that cannot see the "
                       "future, a frozen 2026 market and paper-trading agents.",
        "long_description": (
            "Ask Claude what regime the US market is in and which macro events are close, scan the watchlist, "
            "backtest built-in strategies or one you describe in words, run paper-trading agents on simulated money, "
            "and run research experiments on a frozen point-in-time copy of the 2026 market with a look-ahead check. "
            "Every tool is read-only or works on local paper accounts; none can trade a real account. "
            f"Nothing here is investment advice. Source and documentation: {REPO}"
        ),
        "author": {"name": "Risk-Aware Trading Agents contributors", "url": REPO},
        "repository": {"type": "git", "url": f"{REPO}.git"},
        "homepage": SITE,
        "documentation": f"{REPO}#-mcp-server",
        "support": f"{REPO}/issues",
        "server": {
            "type": "uv",
            "entry_point": "src/server.py",
            "mcp_config": {"command": "uv", "args": ["run", "--directory", "${__dirname}", "src/server.py"]},
        },
        "tools": tool_list,
        "keywords": ["trading", "stocks", "backtesting", "paper trading", "market data", "news", "VIX", "research"],
        "license": "MIT",
        "compatibility": {"platforms": ["darwin", "win32", "linux"], "runtimes": {"python": ">=3.10"}},
    }


def pyproject(version: str, wheel: Path | None = None) -> str:
    requirement = f"{DIST} @ {wheel.resolve().as_uri()}" if wheel else f"{DIST}=={version}"
    return (
        "[project]\n"
        f'name = "{DIST}-mcpb"\n'
        f'version = "{version}"\n'
        'description = "Claude Desktop extension for Risk-Aware Trading Agents"\n'
        'requires-python = ">=3.10"\n'
        f'dependencies = ["{requirement}"]\n'
    )


def build(out_dir: Path, wheel: Path | None = None, root: Path = ROOT) -> Path:
    version = project_version(root)
    files = {
        "manifest.json": json.dumps(manifest(version, tools(root)), indent=2, ensure_ascii=False) + "\n",
        "pyproject.toml": pyproject(version, wheel),
        "src/server.py": SERVER_PY,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{DIST}-{version}.mcpb"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items():
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, text)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist-mcpb", help="folder for the .mcpb file")
    parser.add_argument("--wheel", type=Path, help="depend on this local wheel instead of the PyPI release")
    args = parser.parse_args()
    print(build(args.out, args.wheel))


if __name__ == "__main__":
    main()
