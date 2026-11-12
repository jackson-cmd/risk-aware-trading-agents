"""Register the riskaware MCP server with an AI app or command-line tool.

``riskaware mcp-setup CLIENT`` prints the one command (or the JSON snippet) that adds the server to that client;
``--apply`` runs it. Paths are absolute, because desktop apps do not share your shell's PATH. ChatGPT itself only
connects to remote (HTTPS) MCP servers, so on the OpenAI side use the Codex CLI, which runs on your ChatGPT login.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CLIENTS = ("claude-code", "claude-desktop", "codex", "gemini", "antigravity")
NAME = "riskaware"


def server_command() -> list[str]:
    """How a client should start the server: the installed ``riskaware`` executable, else this Python."""
    exe = shutil.which("riskaware")
    if exe:
        return [str(Path(exe).resolve()), "mcp"]
    here = Path(sys.executable).with_name("riskaware")
    if here.exists():
        return [str(here), "mcp"]
    return [sys.executable, "-m", "riskaware", "mcp"]


def desktop_config_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", Path.home())) / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def setup_command(client: str, server: list[str] | None = None, tool: str | None = None) -> list[str]:
    """The command that registers the server with a command-line client (not for claude-desktop)."""
    server = server or server_command()
    if client == "claude-code":
        return [tool or "claude", "mcp", "add", "-s", "user", NAME, "--", *server]
    if client == "codex":
        return [tool or "codex", "mcp", "add", NAME, "--", *server]
    if client == "gemini":
        return [tool or "gemini", "mcp", "add", "-s", "user", NAME, *server]
    if client == "antigravity":
        return [tool or "agy", "mcp", "add", NAME, *server]
    raise ValueError(f"unknown client '{client}'; choose one of {', '.join(CLIENTS)}")


def desktop_snippet(server: list[str] | None = None) -> dict:
    server = server or server_command()
    return {"mcpServers": {NAME: {"command": server[0], "args": server[1:]}}}


def apply_desktop(path: Path | None = None, server: list[str] | None = None) -> str:
    """Add the server to Claude Desktop's config, keeping every other entry and a backup of the old file."""
    path = path or desktop_config_path()
    data: dict = {}
    mode = 0o600  # the file can hold other servers' tokens: never make it, or its backup, readable by others
    if path.is_file():
        mode = stat.S_IMODE(path.stat().st_mode)
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
        backup = path.with_name(f"{path.name}.{datetime.now():%Y%m%d-%H%M%S}.bak")
        _write_private(backup, text, mode)
    entry = desktop_snippet(server)["mcpServers"][NAME]
    data.setdefault("mcpServers", {})[NAME] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    _write_private(tmp, json.dumps(data, indent=2, ensure_ascii=False) + "\n", mode)
    tmp.replace(path)
    return f"added '{NAME}' to {path}; restart Claude Desktop to load it"


def _write_private(path: Path, text: str, mode: int) -> None:
    """Create the file with ``mode`` from the start, so it is never readable by others, even briefly."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, mode)  # O_CREAT's mode is reduced by the umask


def apply(client: str, tool: str | None = None) -> str:
    if client == "claude-desktop":
        return apply_desktop()
    cmd = setup_command(client, tool=tool)
    if not shutil.which(cmd[0]):
        raise RuntimeError(f"'{cmd[0]}' is not installed or not on PATH")
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout).strip()[:400] or f"exit {res.returncode}")
    return (res.stdout or res.stderr).strip()[:400] or f"registered '{NAME}' with {client}"
