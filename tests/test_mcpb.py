"""The Claude Desktop extension the release attaches (scripts/build_mcpb.py)."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

from riskaware import mcp_server

ROOT = Path(__file__).resolve().parent.parent


def _builder():
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts" / "build_mcpb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_bundle_installs_this_version_from_pypi(tmp_path):
    mod = _builder()
    version = mod.project_version()
    path = mod.build(tmp_path)
    assert path.name == f"risk-aware-trading-agents-{version}.mcpb"
    with zipfile.ZipFile(path) as archive:
        assert sorted(archive.namelist()) == ["manifest.json", "pyproject.toml", "src/server.py"]
        manifest = json.loads(archive.read("manifest.json"))
        pyproject = archive.read("pyproject.toml").decode()
        server = archive.read("src/server.py").decode()
    assert manifest["manifest_version"] == "0.4" and manifest["server"]["type"] == "uv"
    assert manifest["version"] == version
    assert f'"risk-aware-trading-agents=={version}"' in pyproject
    assert "from riskaware.mcp_server import main" in server
    assert [t["name"] for t in manifest["tools"]] == [f.__name__ for f in mcp_server.TOOLS]
    assert all(t["description"] for t in manifest["tools"])
    assert mod.build(tmp_path).read_bytes() == path.read_bytes(), "the same version gives the same archive"


def test_a_local_wheel_can_stand_in_for_pypi(tmp_path):
    mod = _builder()
    wheel = tmp_path / "risk_aware_trading_agents-0-py3-none-any.whl"
    wheel.write_bytes(b"")
    with zipfile.ZipFile(mod.build(tmp_path / "out", wheel)) as archive:
        assert wheel.resolve().as_uri() in archive.read("pyproject.toml").decode()
