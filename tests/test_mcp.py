import enum
from dataclasses import dataclass
from datetime import date

import numpy as np
import pytest

from riskaware import mcp_server as m


class Color(enum.Enum):
    RED = "red"


@dataclass
class Box:
    day: date
    color: Color
    values: list


def test_plain_makes_json_friendly_data():
    out = m._plain({"a": np.float64(1.23456789), "b": float("nan"), "c": Box(date(2026, 1, 2), Color.RED, [1, (2, 3)]),
                    "d": np.int64(7)})
    assert out == {"a": 1.234568, "b": None, "c": {"day": "2026-01-02", "color": "red", "values": [1, [2, 3]]},
                   "d": 7}


def test_agent_tools_create_and_read(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    created = m.agents_create("mcp-test", strategy="ema_bounce", profile="conservative", universe="etf", cash=5_000)
    assert created["created"]["name"] == "mcp-test" and created["created"]["cash"] == 5_000
    detail = m.agent_detail("mcp-test")
    assert detail["agent"]["strategy"] == "ema_bounce" and detail["cash"] == 5_000
    assert detail["positions"] == {} and detail["recent_orders"] == []
    with pytest.raises(ValueError):
        m.agents_create("mcp-test")
    with pytest.raises(ValueError):
        m.agents_create("bad name")


def test_list_strategies_tool():
    names = {s["name"] for s in m.list_strategies()["strategies"]}
    assert {"risk_aware", "wheel", "momentum_rotation"} <= names


def test_server_registers_every_tool():
    pytest.importorskip("mcp")
    import asyncio

    server = m.build_server()
    tools = asyncio.run(server.list_tools())
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {f.__name__ for f in m.TOOLS}
    def read_only(name):  # SDK 1.x and 2.x name the field differently; the wire alias is the same
        return by_name[name].annotations.model_dump(by_alias=True)["readOnlyHint"]

    assert read_only("market_regime") is True
    assert read_only("agents_run") is False
    # nothing that could reach a live broker
    assert not any("order" in n or "live" in n for n in by_name)
