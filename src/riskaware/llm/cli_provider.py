"""Use an AI subscription instead of an API key: the review runs through a command-line tool you are already
logged in to.

- ``claude-cli``: Claude Code (``claude``), your Claude Pro / Max / Team login
- ``codex-cli``: OpenAI Codex (``codex``), your ChatGPT login
- ``gemini-cli``: Google's Gemini CLI (``gemini``) or Antigravity (``agy``), your Google login

Each review starts the tool once in an empty temporary folder, with its tools switched off where the tool allows
it, and asks for the same strict JSON schema as the API providers. The tools add their own system prompts, so a
review is slower (5 to 20 seconds) and uses more of your plan's quota than an API call; nothing is billed per
token. Set ``RISKAWARE_CLAUDE_CLI``, ``RISKAWARE_CODEX_CLI`` or ``RISKAWARE_GEMINI_CLI`` (or ``llm.command``) to
use a different executable or a wrapper script.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..config import LLMConfig
from .openai_compat import parse_json_object

log = logging.getLogger(__name__)

KINDS = ("claude-cli", "codex-cli", "gemini-cli")
ENV = {"claude-cli": "RISKAWARE_CLAUDE_CLI", "codex-cli": "RISKAWARE_CODEX_CLI", "gemini-cli": "RISKAWARE_GEMINI_CLI"}


def default_command(kind: str) -> str:
    """The executable for a kind: the environment override, else the usual name on PATH."""
    if os.environ.get(ENV[kind]):
        return os.environ[ENV[kind]]
    if kind == "claude-cli":
        return "claude"
    if kind == "codex-cli":
        return "codex"
    return "gemini" if shutil.which("gemini") else "agy"


def _is_antigravity(command: str) -> bool:
    name = Path(command.split()[0]).name.lower()
    return name.startswith("agy") or "antigravity" in name


class CLIProvider:
    """Runs one review through a logged-in AI command-line tool (see the module docstring)."""

    def __init__(self, cfg: LLMConfig):
        if cfg.provider not in KINDS:
            raise ValueError(f"not a CLI provider: {cfg.provider}")
        self.cfg = cfg
        self.kind = cfg.provider
        self.name = self.kind
        self.model = "" if cfg.model in ("", "default") else cfg.model
        self.command = cfg.command or default_command(self.kind)
        self.last_raw: str | None = None
        self.last_error: str | None = None
        self.last_usage: dict[str, Any] = {}

    # ------------------------------------------------------------------------------- commands
    def argv(self, system: str, user: str, schema_file: Path, out_file: Path) -> tuple[list[str], str | None]:
        """The command line and the text for stdin."""
        exe = self.command.split()
        if self.kind == "claude-cli":
            args = [*exe, "-p", "--no-session-persistence", "--tools", "", "--strict-mcp-config",
                    "--setting-sources", "local", "--output-format", "json", "--system-prompt", system,
                    "--json-schema", schema_file.read_text(encoding="utf-8")]
            return args + (["--model", self.model] if self.model else []), user
        if self.kind == "codex-cli":
            args = [*exe, "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--color", "never",
                    "--output-schema", str(schema_file), "-o", str(out_file)]
            return args + (["-m", self.model] if self.model else []) + ["-"], f"{system}\n\n{user}"
        prompt = f"{system}\n\n{user}"
        if _is_antigravity(self.command):
            args = [*exe, "-p", prompt, "--json-schema", str(schema_file), "--output-format", "json",
                    "--print-timeout", f"{int(self.cfg.timeout)}s"]
            return args + (["--model", self.model] if self.model else []), None
        args = [*exe, "-p", prompt, "--output-format", "json"]  # Google's Gemini CLI
        return args + (["-m", self.model] if self.model else []), None

    # ------------------------------------------------------------------------------- parsing
    def parse(self, stdout: str, stderr: str, out_file: Path) -> dict[str, Any] | None:
        if self.kind == "codex-cli":
            m = re.search(r"tokens used\s*\n?\s*([\d,]+)", stderr + "\n" + stdout)
            if m:
                self.last_usage = {"total_tokens": int(m.group(1).replace(",", ""))}
            text = out_file.read_text(encoding="utf-8") if out_file.is_file() else ""
            self.last_raw = text
            return parse_json_object(text)
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            self.last_raw = stdout[-4000:]
            return parse_json_object(stdout)
        if self.kind == "claude-cli":
            self.last_usage = {"total_cost_usd": data.get("total_cost_usd"), **(data.get("usage") or {})}
            self.last_raw = data.get("result") if isinstance(data.get("result"), str) else json.dumps(data)[:4000]
            if data.get("is_error"):
                self.last_error = str(data.get("result") or "the CLI reported an error")[:300]
                return None
            if isinstance(data.get("structured_output"), dict):
                return data["structured_output"]
            return parse_json_object(self.last_raw or "")
        # gemini-cli: Antigravity ({response, structured_output, usage}) or Gemini CLI ({response, stats, error})
        self.last_usage = dict(data.get("usage") or data.get("stats") or {})
        self.last_raw = data.get("response") if isinstance(data.get("response"), str) else json.dumps(data)[:4000]
        if data.get("error"):
            self.last_error = str(data.get("error"))[:300]
        if isinstance(data.get("structured_output"), dict):
            return data["structured_output"]
        return parse_json_object(self.last_raw or "")

    # ------------------------------------------------------------------------------- the call
    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any] | None:
        self.last_raw, self.last_error, self.last_usage = None, None, {}
        resolved = shutil.which(self.command.split()[0])
        if not resolved:
            self.last_error = (f"'{self.command}' is not installed or not on PATH; install it and log in, or set "
                               f"{ENV[self.kind]}")
            return None
        with tempfile.TemporaryDirectory(prefix="riskaware-llm-") as tmp:
            folder = Path(tmp)
            schema_file, out_file = folder / "schema.json", folder / "reply.txt"
            schema_file.write_text(json.dumps(schema), encoding="utf-8")
            args, stdin = self.argv(system, user, schema_file, out_file)
            args[0] = resolved  # the full path, so Windows finds npm's .cmd shims too
            try:
                proc = subprocess.run(args, input=stdin, capture_output=True, text=True, cwd=folder,
                                      timeout=self.cfg.timeout, check=False)
            except subprocess.TimeoutExpired:
                self.last_error = f"{self.kind} took longer than {int(self.cfg.timeout)} s"
                return None
            except OSError as exc:
                self.last_error = f"{self.kind} could not start: {exc}"
                return None
            parsed = self.parse(proc.stdout, proc.stderr, out_file)
        if parsed is None and self.last_error is None:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [""]
            self.last_error = (f"exit {proc.returncode}: {tail[0][:200]}" if proc.returncode
                               else "the reply was not a JSON object")
        return parsed
