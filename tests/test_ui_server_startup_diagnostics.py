"""/api/state shape tests for startup_diagnostics on the local instance.

Acceptance test #7: given a broker with two diagnostics (one INFO, one
WARN), the local instance object in `/api/state` carries
`startup_diagnostics` as a length-2 list of dicts each with keys
``{level, message, source, timestamp, category}``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from claude_crew.broker import Broker, BrokerSnapshot
from claude_crew.diagnostics import StartupDiagnostic
from claude_crew.ui_server import UIServer


def _diag(
    level: str,
    message: str,
    source: str,
    timestamp: float,
    category: str,
) -> StartupDiagnostic:
    return StartupDiagnostic(
        level=level,
        message=message,
        source=source,
        timestamp=timestamp,
        category=category,
    )


class TestBuildLocalInstanceStartupDiagnostics:
    """Unit-level: _build_local_instance translates snapshot → instance dict."""

    def test_empty_default_is_empty_list(self) -> None:
        broker = Broker()
        ui = UIServer(broker=broker, port=0)
        snap = BrokerSnapshot(crew_id="c1", teammates=(), live=(), log=())
        instance, _ = ui._build_local_instance(snap)
        assert instance["startup_diagnostics"] == []

    def test_two_diagnostics_serialize_as_list_of_dicts(self) -> None:
        diags = (
            _diag("INFO",
                  "explorer.md shadows default-pack explorer",
                  "claude_crew.subagents.loader",
                  1715000000.123,
                  "shadow"),
            _diag("WARNING",
                  "extra_skills: skill 'nope' not found",
                  "claude_crew.factories",
                  1715000001.456,
                  "unknown_skill"),
        )
        broker = Broker()
        ui = UIServer(broker=broker, port=0)
        snap = BrokerSnapshot(
            crew_id="c1",
            teammates=(),
            live=(),
            log=(),
            startup_diagnostics=diags,
        )
        instance, _ = ui._build_local_instance(snap)
        sd = instance["startup_diagnostics"]
        assert isinstance(sd, list)
        assert len(sd) == 2
        for entry in sd:
            assert isinstance(entry, dict)
            assert set(entry.keys()) == {
                "level", "message", "source", "timestamp", "category",
            }
        assert sd[0]["level"] == "INFO"
        assert sd[0]["category"] == "shadow"
        assert sd[0]["source"] == "claude_crew.subagents.loader"
        assert sd[0]["timestamp"] == pytest.approx(1715000000.123)
        assert sd[1]["level"] == "WARNING"
        assert sd[1]["category"] == "unknown_skill"
        assert "nope" in sd[1]["message"]

    def test_order_preserved(self) -> None:
        diags = tuple(
            _diag("INFO", f"msg-{i}", "claude_crew.subagents.loader",
                  1700000000.0 + i, "shadow")
            for i in range(5)
        )
        broker = Broker()
        ui = UIServer(broker=broker, port=0)
        snap = BrokerSnapshot(
            crew_id="c1",
            teammates=(),
            live=(),
            log=(),
            startup_diagnostics=diags,
        )
        instance, _ = ui._build_local_instance(snap)
        messages = [d["message"] for d in instance["startup_diagnostics"]]
        assert messages == [f"msg-{i}" for i in range(5)]


class TestApiStateShape:
    """Acceptance test #7: HTTP /api/state contract surface."""

    def test_api_state_local_instance_has_startup_diagnostics(self) -> None:
        diags = (
            _diag("INFO",
                  "explorer.md shadows default-pack explorer",
                  "claude_crew.subagents.loader",
                  1715000000.123,
                  "shadow"),
            _diag("WARNING",
                  "extra_skills: skill 'nope' not found",
                  "claude_crew.factories",
                  1715000001.456,
                  "unknown_skill"),
        )
        broker = Broker(startup_diagnostics=diags)
        ui = UIServer(broker=broker, port=0)
        client = TestClient(ui._make_app())

        resp = client.get("/api/state?local=1")
        assert resp.status_code == 200
        data = resp.json()
        local = next(i for i in data["instances"] if i.get("is_local"))
        sd = local["startup_diagnostics"]
        assert isinstance(sd, list)
        assert len(sd) == 2
        keys = {"level", "message", "source", "timestamp", "category"}
        for entry in sd:
            assert keys == set(entry.keys())
        assert sd[0]["level"] == "INFO"
        assert sd[1]["level"] == "WARNING"

    def test_api_state_empty_default(self) -> None:
        broker = Broker()
        ui = UIServer(broker=broker, port=0)
        client = TestClient(ui._make_app())
        resp = client.get("/api/state?local=1")
        assert resp.status_code == 200
        local = next(i for i in resp.json()["instances"] if i.get("is_local"))
        assert local["startup_diagnostics"] == []
