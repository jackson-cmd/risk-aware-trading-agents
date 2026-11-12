"""Install a background job that runs the paper-trading agents every trading day.

The job fires every 15 minutes on weekdays and calls ``riskaware agents run --scheduled``, which trades each
agent once per trading day after ``execution.run_time`` (New York) and before the close, and does nothing the
rest of the time. Firing often instead of once keeps it right across time zones and daylight saving, and lets
a laptop that was asleep at 15:40 catch up when it wakes.

- macOS: a LaunchAgent in ~/Library/LaunchAgents
- Linux: a line in your crontab
- Windows: a Task Scheduler task
"""

from __future__ import annotations

import os
import platform
import plistlib
import shlex
import subprocess
import sys
from pathlib import Path

LABEL = "io.github.risk-aware-trading-agents"
CRON_MARK = "# riskaware-agents"
WIN_TASK = "RiskAwareTradingAgents"


def command(workdir: Path, config: Path | None = None, python: str | None = None) -> list[str]:
    cmd = [python or sys.executable, "-m", "riskaware"]
    if config:
        cmd += ["--config", str(config)]
    return cmd + ["agents", "run", "--scheduled", "--cwd", str(workdir)]


def launchd_plist(cmd: list[str], workdir: Path, log_file: Path, env: dict[str, str] | None = None) -> bytes:
    intervals = [{"Weekday": wd, "Minute": m} for wd in range(1, 6) for m in (0, 15, 30, 45)]
    data = {"Label": LABEL, "ProgramArguments": cmd, "WorkingDirectory": str(workdir),
            "StartCalendarInterval": intervals, "StandardOutPath": str(log_file),
            "StandardErrorPath": str(log_file), "RunAtLoad": False, "ProcessType": "Background"}
    if env:
        data["EnvironmentVariables"] = env
    return plistlib.dumps(data)


def cron_line(cmd: list[str], log_file: Path) -> str:
    return f"*/15 * * * 1-5 {shlex.join(cmd)} >> {shlex.quote(str(log_file))} 2>&1 {CRON_MARK}"


def schtasks_create(cmd: list[str]) -> list[str]:
    tr = subprocess.list2cmdline(cmd)
    return ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "15", "/TN", WIN_TASK, "/TR", tr]


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _env() -> dict[str, str]:
    keep = {k: os.environ[k] for k in ("RISKAWARE_HOME", "RISKAWARE_LANG") if os.environ.get(k)}
    keep["PATH"] = os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return keep


def system() -> str:
    s = platform.system()
    return {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(s, s.lower())


def _run(args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, input=input_text, capture_output=True, text=True, check=False)


def install(workdir: Path, log_file: Path, config: Path | None = None) -> str:
    """Install (or replace) the background job. Returns a one-line description."""
    workdir = workdir.resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = command(workdir, config.resolve() if config else None)
    kind = system()
    if kind == "macos":
        path = _plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", f"{domain}/{LABEL}"])
        path.write_bytes(launchd_plist(cmd, workdir, log_file, _env()))
        res = _run(["launchctl", "bootstrap", domain, str(path)])
        if res.returncode != 0:
            raise RuntimeError(f"launchctl bootstrap failed: {res.stderr.strip() or res.stdout.strip()}")
        return f"LaunchAgent {path}"
    if kind == "linux":
        current = _run(["crontab", "-l"])
        lines = [ln for ln in (current.stdout.splitlines() if current.returncode == 0 else []) if CRON_MARK not in ln]
        lines.append(cron_line(cmd, log_file))
        res = _run(["crontab", "-"], "\n".join(lines) + "\n")
        if res.returncode != 0:
            raise RuntimeError(f"crontab failed: {res.stderr.strip()}")
        return "crontab entry (every 15 minutes on weekdays)"
    if kind == "windows":
        res = _run(schtasks_create(cmd))
        if res.returncode != 0:
            raise RuntimeError(f"schtasks failed: {res.stderr.strip() or res.stdout.strip()}")
        return f"Task Scheduler task '{WIN_TASK}'"
    raise RuntimeError(f"automatic install is not supported on {platform.system()}; run "
                       f"`{shlex.join(cmd)}` every 15 minutes with your own scheduler")


def uninstall() -> str:
    kind = system()
    if kind == "macos":
        path = _plist_path()
        _run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"])
        existed = path.exists()
        path.unlink(missing_ok=True)
        return f"removed {path}" if existed else "no LaunchAgent was installed"
    if kind == "linux":
        current = _run(["crontab", "-l"])
        if current.returncode != 0 or CRON_MARK not in current.stdout:
            return "no crontab entry was installed"
        lines = [ln for ln in current.stdout.splitlines() if CRON_MARK not in ln]
        _run(["crontab", "-"], "\n".join(lines) + "\n")
        return "removed the crontab entry"
    if kind == "windows":
        res = _run(["schtasks", "/Delete", "/F", "/TN", WIN_TASK])
        return f"removed task '{WIN_TASK}'" if res.returncode == 0 else "no task was installed"
    return "nothing to remove"


def installed() -> bool:
    kind = system()
    if kind == "macos":
        return _plist_path().exists()
    if kind == "linux":
        res = _run(["crontab", "-l"])
        return res.returncode == 0 and CRON_MARK in res.stdout
    if kind == "windows":
        return _run(["schtasks", "/Query", "/TN", WIN_TASK]).returncode == 0
    return False
