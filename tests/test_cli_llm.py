import json
import stat
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from riskaware import mcp_setup as ms
from riskaware.arena import AgentSpec, Arena
from riskaware.cli import app
from riskaware.llm import LLMReviewer, llm_config, make_provider, parse_choice
from riskaware.llm.cli_provider import CLIProvider
from riskaware.llm.reviewer import SCHEMA
from riskaware.types import Action, Signal

VERDICT = {"bull_case": "On support.", "bear_case": "No catalyst.", "adjustment": 0.04, "veto": False,
           "veto_reason": "", "summary": "Mild support."}


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the fake CLIs are POSIX scripts")


def fake_tool(tmp_path: Path, name: str, body: str) -> str:
    """A stand-in for claude / codex / agy / gemini: a Python script that prints what the real tool prints."""
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\nimport json, sys, time\n{body}\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_choices_for_subscriptions():
    assert parse_choice("codex-cli") == ("codex-cli", "default")
    assert parse_choice("claude-cli:sonnet") == ("claude-cli", "sonnet")
    c = llm_config("gemini-cli:gemini-3.8-flash-low", command="agyw")
    assert c.provider == "gemini-cli" and c.command == "agyw" and c.timeout >= 240 and c.base_url is None
    assert isinstance(make_provider(c), CLIProvider)
    spec = AgentSpec(name="sub", llm="codex-cli", llm_command="/opt/bin/codex")
    assert spec.llm_label == "codex-cli" and AgentSpec(name="g", llm="gemini-cli:x").llm_label == "gemini-cli x"


def test_command_lines(tmp_path):
    schema, out = tmp_path / "s.json", tmp_path / "o.txt"
    schema.write_text(json.dumps(SCHEMA))
    claude = CLIProvider(llm_config("claude-cli:sonnet"))
    args, stdin = claude.argv("SYS", "USER", schema, out)
    assert args[:2] == ["claude", "-p"] and stdin == "USER"
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA and args[-2:] == ["--model", "sonnet"]
    assert "--bare" not in args  # --bare would ignore the subscription login
    codex = CLIProvider(llm_config("codex-cli"))
    args, stdin = codex.argv("SYS", "USER", schema, out)
    assert args[:2] == ["codex", "exec"] and args[-1] == "-" and "-m" not in args and stdin == "SYS\n\nUSER"
    assert args[args.index("--output-schema") + 1] == str(schema) and args[args.index("-o") + 1] == str(out)
    agy = CLIProvider(llm_config("gemini-cli:gemini-3.8-flash-low", command="agyw"))
    args, stdin = agy.argv("SYS", "USER", schema, out)
    assert args[0] == "agyw" and "--json-schema" in args and "--print-timeout" in args and stdin is None
    gem = CLIProvider(llm_config("gemini-cli:gemini-2.5-flash", command="gemini"))
    args, _ = gem.argv("SYS", "USER", schema, out)
    assert args[:2] == ["gemini", "-p"] and args[-2:] == ["-m", "gemini-2.5-flash"] and "--json-schema" not in args


@posix_only
def test_claude_cli_end_to_end(tmp_path):
    reply = {"type": "result", "is_error": False, "result": json.dumps(VERDICT), "structured_output": VERDICT,
             "total_cost_usd": 0.0, "usage": {"input_tokens": 900, "output_tokens": 80}}
    exe = fake_tool(tmp_path, "claude", f"sys.stdin.read(); print(json.dumps({reply!r}))")
    p = CLIProvider(llm_config("claude-cli:sonnet", command=exe))
    assert p.complete_json("s", "u", SCHEMA) == VERDICT
    assert p.last_usage["input_tokens"] == 900 and p.last_error is None
    expired = {"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth session expired"}
    bad = fake_tool(tmp_path, "claude2", f"sys.stdin.read(); print(json.dumps({expired!r})); sys.exit(1)")
    q = CLIProvider(llm_config("claude-cli", command=bad))
    assert q.complete_json("s", "u", SCHEMA) is None and "OAuth session expired" in q.last_error


@posix_only
def test_codex_cli_end_to_end(tmp_path):
    body = ("sys.stdin.read(); out = sys.argv[sys.argv.index('-o') + 1]\n"
            f"open(out, 'w').write(json.dumps({VERDICT!r}))\n"
            "sys.stderr.write('codex\\n...\\ntokens used\\n17,576\\n')")
    p = CLIProvider(llm_config("codex-cli", command=fake_tool(tmp_path, "codex", body)))
    assert p.complete_json("s", "u", SCHEMA) == VERDICT and p.last_usage == {"total_tokens": 17576}


@posix_only
def test_antigravity_and_gemini_cli_end_to_end(tmp_path):
    agy_reply = {"status": "SUCCESS", "response": "```json\n{}\n```", "structured_output": VERDICT,
                 "usage": {"total_tokens": 12237}}
    agy = CLIProvider(llm_config("gemini-cli", command=fake_tool(tmp_path, "agy", f"print(json.dumps({agy_reply!r}))")))
    assert agy.complete_json("s", "u", SCHEMA) == VERDICT and agy.last_usage["total_tokens"] == 12237
    gem_reply = {"response": "Here you go:\n```json\n" + json.dumps(VERDICT) + "\n```", "stats": {"models": {}}}
    gem = CLIProvider(llm_config("gemini-cli", command=fake_tool(tmp_path, "gemini",
                                                                 f"print(json.dumps({gem_reply!r}))")))
    assert gem.complete_json("s", "u", SCHEMA) == VERDICT


@posix_only
def test_missing_slow_and_broken_tools(tmp_path):
    missing = CLIProvider(llm_config("codex-cli", command=str(tmp_path / "no-such-codex")))
    assert missing.complete_json("s", "u", SCHEMA) is None and "not installed" in missing.last_error
    c = llm_config("gemini-cli", command=fake_tool(tmp_path, "agy-slow", "time.sleep(5)"))
    c.timeout = 0.5
    slow = CLIProvider(c)
    assert slow.complete_json("s", "u", SCHEMA) is None and "longer than" in slow.last_error
    broken = CLIProvider(llm_config("gemini-cli", command=fake_tool(tmp_path, "agy-broken",
                                                                    "sys.stderr.write('boom'); sys.exit(3)")))
    assert broken.complete_json("s", "u", SCHEMA) is None and broken.last_error.startswith("exit 3")


@posix_only
def test_reviewer_through_a_subscription(tmp_path):
    reply = {"type": "result", "is_error": False, "result": "", "structured_output": VERDICT}
    exe = fake_tool(tmp_path, "claude", f"sys.stdin.read(); print(json.dumps({reply!r}))")
    cfg = llm_config("claude-cli:sonnet", command=exe)
    rv = LLMReviewer(make_provider(cfg), cfg)

    class Market:
        regime, vix, fear_greed, event_risk, reasons = "NEUTRAL", 17.0, 45.0, 0.1, []

    v = rv.review(Signal("ACME", Action.BUY, 0.6, "on support", setups=["ema200_support"]), Market())
    assert v.adjustment == pytest.approx(0.04) and v.provider == "claude-cli"
    rec = rv.pop_records()[0]
    assert rec.model == "sonnet" and rec.error is None


def test_agent_with_a_subscription_reviewer(tmp_path, offline_cfg):
    arena = Arena(tmp_path / "arena", base_cfg=offline_cfg)
    spec = arena.add(AgentSpec(name="plan", llm="gemini-cli:gemini-3.8-flash-low", llm_command="agyw"))
    c = arena.config_for(spec)
    assert c.llm.provider == "gemini-cli" and c.llm.command == "agyw" and c.llm.model == "gemini-3.8-flash-low"
    assert arena.set_llm("plan", "codex-cli", command="/x/codex").llm_command == "/x/codex"


def test_mcp_setup_commands_and_desktop_config(tmp_path, monkeypatch):
    server = ["/opt/riskaware", "mcp"]
    assert ms.setup_command("claude-code", server) == ["claude", "mcp", "add", "-s", "user", "riskaware", "--",
                                                       "/opt/riskaware", "mcp"]
    assert ms.setup_command("codex", server)[:4] == ["codex", "mcp", "add", "riskaware"]
    assert ms.setup_command("antigravity", server, tool="agyw")[0] == "agyw"
    with pytest.raises(ValueError):
        ms.setup_command("chatgpt", server)
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    msg = ms.apply_desktop(cfg, server)
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["riskaware"] == {"command": "/opt/riskaware", "args": ["mcp"]}
    assert data["mcpServers"]["other"] == {"command": "x"} and data["theme"] == "dark"
    assert list(cfg.parent.glob("*.bak")) and "restart Claude Desktop" in msg
    if sys.platform != "win32":  # a private config stays private, and so does its backup
        cfg.chmod(0o600)
        ms.apply_desktop(cfg, server)
        assert oct(cfg.stat().st_mode & 0o777) == "0o600"
        assert all(oct(b.stat().st_mode & 0o777) == "0o600" for b in sorted(cfg.parent.glob("*.bak"))[-1:])
    monkeypatch.setattr(ms.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError):
        ms.apply("codex")


def test_cli_mcp_setup_prints_a_command():
    res = CliRunner().invoke(app, ["mcp-setup", "codex"])
    # stdout is only the command (a hint about the [mcp] extra may go to stderr)
    assert res.exit_code == 0 and res.stdout.startswith("codex mcp add riskaware -- ")
    res = CliRunner().invoke(app, ["mcp-setup", "claude-desktop"])
    assert res.exit_code == 0 and '"riskaware"' in res.output
    assert CliRunner().invoke(app, ["mcp-setup", "chatgpt"]).exit_code == 1


def test_install_hints_keep_the_extra_name(monkeypatch):
    """Rich markup must not eat "[ibkr]" or "[mcp]" from the pip commands it prints."""
    res = CliRunner().invoke(app, ["brokers"], env={"COLUMNS": "220"})
    assert res.exit_code == 0 and 'risk-aware-trading-agents[ibkr]' in res.output
    import importlib.util as iu

    real = iu.find_spec
    monkeypatch.setattr(iu, "find_spec", lambda name, *a, **k: None if name == "mcp" else real(name, *a, **k))
    res = CliRunner().invoke(app, ["mcp-setup", "codex"])
    assert 'risk-aware-trading-agents[mcp]' in res.stderr and res.stdout.startswith("codex mcp add riskaware")
