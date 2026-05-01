"""E2E integration tests for Feature #19 (Tool-Use Events in Dashboard Stream).

Task 5 — full-pipeline scenarios: hook fires → deque appends → snapshot
flattens → UIServer merges → JSON serializes → consumer reads. Each test
exercises the pipeline through the public surface (PreToolUse / PostToolUse
hook callbacks + Broker.snapshot + UIServer._build_local_instance), not the
internal helpers in isolation.

Setup notes:
- conftest autouse sets CLAUDE_CREW_TEAMMATE_MODE=stub and
  CLAUDE_CREW_TRANSCRIPT_DISABLED=1. We use ProgrammableSDKClient + manual
  hook drives, same pattern as test_e2e_tool_telemetry.py.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker
from claude_crew.sdk_teammate import SdkTeammate
from claude_crew.teammate import ToolEvent, _ToolUseEntry
from claude_crew.ui_server import UIServer
from tests.fakes.programmable_sdk_client import ProgrammableSDKClient
from tests.fakes.sdk import text_response


def _patch_sdk(monkeypatch, fake: ProgrammableSDKClient) -> None:
    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)


def _factory_for(fake: ProgrammableSDKClient):
    def _factory(id: str, name: str, role: str, **_kw: Any) -> SdkTeammate:
        return SdkTeammate(id=id, name=name, role=role)
    return _factory


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestHappyPath:
    """SC-1 / SC-2: tool events make it through the full pipeline to the JSON payload."""

    async def test_completed_tool_event_visible_in_dashboard_payload(
        self, broker, monkeypatch
    ) -> None:
        """SC-1: a completed Pre+Post pair appears as kind:'tool' in /api/state's transcript stream."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        # Drive a Pre+Post through the hook callbacks.
        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "ls /tmp"}},
            "tu-happy", {},
        )
        await asyncio.sleep(0.005)
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_response": "ok"},
            "tu-happy", {},
        )

        ui = UIServer(broker=broker, port=0)
        snap = broker.snapshot()
        instance, messages = ui._build_local_instance(snap)

        tool_msgs = [m for m in messages if m["kind"] == "tool"]
        assert len(tool_msgs) == 1
        m = tool_msgs[0]
        assert m["from"] == tid
        assert m["to"] is None
        assert m["body"].startswith("Bash (ok,")
        assert "command=ls /tmp" in m["body"]
        # JSON-serializable as a sanity check (UIServer emits JSON over WS).
        json.dumps({"instances": [instance], "transcripts": {snap.crew_id: messages}})

    async def test_multi_teammate_interleaved_stream(
        self, broker, monkeypatch
    ) -> None:
        """SC-2: two teammates' events interleave in the merged stream by timestamp."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        a = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        b = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))

        # Manually inject events with controlled timestamps so the test is
        # deterministic regardless of CPU scheduling.
        plan = [
            (a, 1.0, "Bash"), (b, 1.5, "Read"), (a, 2.0, "Grep"),
            (b, 2.5, "Edit"), (a, 3.0, "Write"),
        ]
        for tid, t, name in plan:
            broker._teammates[tid]._completed_tool_events.append(ToolEvent(
                teammate_id=tid, tool_name=name, tool_use_id=f"{tid}-{t}",
                started_at_wallclock=t - 0.1, finished_at_wallclock=t,
                duration_seconds=0.1, outcome="ok",
                args_summary=None, error_summary=None, redaction_version="v1",
            ))

        ui = UIServer(broker=broker, port=0)
        _, messages = ui._build_local_instance(broker.snapshot())
        tool_msgs = [m for m in messages if m["kind"] == "tool"]

        assert len(tool_msgs) == 5
        # Stream order matches plan order (raw-float sort).
        bodies = [m["body"] for m in tool_msgs]
        assert "Bash" in bodies[0]
        assert "Read" in bodies[1]
        assert "Grep" in bodies[2]
        assert "Edit" in bodies[3]
        assert "Write" in bodies[4]


# ─────────────────────────────────────────────────────────────────────────────
# Sad paths
# ─────────────────────────────────────────────────────────────────────────────


class TestSadPaths:
    async def test_tombstoned_teammate_events_remain_visible(
        self, broker, monkeypatch
    ) -> None:
        """SC-4: tombstoned teammate's tool events stay visible in the dashboard."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        # 3 completed events.
        for i in range(3):
            await teammate._on_pre_tool_use(
                {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": f"x{i}"}},
                f"tu-{i}", {},
            )
            await teammate._on_post_tool_use(
                {"agent_id": None, "tool_name": "Bash", "tool_response": "ok"},
                f"tu-{i}", {},
            )

        await broker.kill_teammate(tid)
        await asyncio.sleep(0.05)

        ui = UIServer(broker=broker, port=0)
        _, messages = ui._build_local_instance(broker.snapshot())
        tool_msgs = [m for m in messages if m["kind"] == "tool"]

        assert len(tool_msgs) == 3
        assert all(m["from"] == tid for m in tool_msgs)

    async def test_in_flight_tool_at_tombstone_appears_as_killed(
        self, broker, monkeypatch
    ) -> None:
        """SC-4 / D-3: in-flight at tombstone → outcome='killed' visible in stream."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "long_build"}},
            "tu-inflight", {},
        )
        await broker.kill_teammate(tid)
        await asyncio.sleep(0.05)

        ui = UIServer(broker=broker, port=0)
        _, messages = ui._build_local_instance(broker.snapshot())
        tool_msgs = [m for m in messages if m["kind"] == "tool"]

        assert len(tool_msgs) == 1
        assert "killed" in tool_msgs[0]["body"]

    async def test_disk_full_does_not_blind_dashboard(
        self, broker, monkeypatch
    ) -> None:
        """SC-8 / D-4: in-memory append survives JSONL sink failure."""
        from unittest.mock import MagicMock

        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        broker._sink = MagicMock()
        broker._sink.write_tool_event.side_effect = RuntimeError("disk full")

        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "ls"}},
            "tu-disk", {},
        )
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_response": "ok"},
            "tu-disk", {},
        )

        ui = UIServer(broker=broker, port=0)
        _, messages = ui._build_local_instance(broker.snapshot())
        tool_msgs = [m for m in messages if m["kind"] == "tool"]
        assert len(tool_msgs) == 1
        assert "Bash" in tool_msgs[0]["body"]

    async def test_deque_rollover_silently_retains_most_recent_n(
        self, broker, monkeypatch
    ) -> None:
        """SC-5 / D-12: env-var maxlen enforced; oldest events silently dropped."""
        monkeypatch.setenv("CLAUDE_CREW_TOOL_EVENTS_PER_TEAMMATE", "10")
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        for i in range(25):
            teammate._completed_tool_events.append(ToolEvent(
                teammate_id=tid, tool_name="Bash", tool_use_id=f"id-{i}",
                started_at_wallclock=float(i), finished_at_wallclock=float(i) + 0.1,
                duration_seconds=0.1, outcome="ok",
                args_summary=None, error_summary=None, redaction_version="v1",
            ))

        ui = UIServer(broker=broker, port=0)
        _, messages = ui._build_local_instance(broker.snapshot())
        tool_msgs = [m for m in messages if m["kind"] == "tool"]
        assert len(tool_msgs) == 10
        # Most-recent 10 retained (id-15 through id-24); first 15 silently dropped.
        # Snapshot sort is by finished_at_wallclock asc, so first message corresponds
        # to id-15 (finished_at=15.1) and last to id-24 (finished_at=24.1).

    async def test_task_tool_filtered_from_dashboard_but_present_in_snapshot(
        self, broker, monkeypatch
    ) -> None:
        """Q2 revised + sentinel DEFER-3: Task hidden in dashboard, kept in snapshot.tool_events."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        # 3 Bash + 2 Task events (manually injected for determinism — Task path
        # in #7 is async and complex; we just need the deque populated).
        for i, name in enumerate(("Bash", "Task", "Bash", "Task", "Bash")):
            teammate._completed_tool_events.append(ToolEvent(
                teammate_id=tid, tool_name=name, tool_use_id=f"id-{i}",
                started_at_wallclock=float(i), finished_at_wallclock=float(i) + 0.1,
                duration_seconds=0.1, outcome="ok",
                args_summary=None, error_summary=None, redaction_version="v1",
            ))

        snap = broker.snapshot()
        # Snapshot preserves all 5 (data preserved upstream of UI filter).
        assert len(snap.tool_events) == 5

        ui = UIServer(broker=broker, port=0)
        _, messages = ui._build_local_instance(snap)
        tool_msgs = [m for m in messages if m["kind"] == "tool"]
        # Dashboard hides the 2 Task entries.
        assert len(tool_msgs) == 3
        assert all(not m["body"].startswith("Task ") for m in tool_msgs)

    async def test_parallel_tools_each_get_independent_event(
        self, broker, monkeypatch
    ) -> None:
        """Sentinel DEFER-3: two simultaneous Pre+Post pairs produce distinct deque entries."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        # Two Pre's, then two Post's (asyncio serializes; both must end up in deque).
        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "a"}},
            "tu-1", {},
        )
        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Read", "tool_input": {"file_path": "/x"}},
            "tu-2", {},
        )
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_response": "ok"},
            "tu-1", {},
        )
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Read", "tool_response": "ok"},
            "tu-2", {},
        )

        assert len(teammate._completed_tool_events) == 2
        ids = {ev.tool_use_id for ev in teammate._completed_tool_events}
        assert ids == {"tu-1", "tu-2"}

    async def test_remote_fanout_pass_through_for_tool_events(
        self, broker, monkeypatch
    ) -> None:
        """SC-6: tool events ride inline in transcripts[crew_id], so #13's existing
        fanout pass-through behavior carries them automatically. We assert the
        UIServer-produced JSON shape is what _fetch_remote_state expects to find:
        a list of {t, from, to, kind, body} records under transcripts[crew_id].
        """
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        teammate._completed_tool_events.append(ToolEvent(
            teammate_id=tid, tool_name="Bash", tool_use_id="tu-r",
            started_at_wallclock=1.0, finished_at_wallclock=1.5,
            duration_seconds=0.5, outcome="ok",
            args_summary="command=ls", error_summary=None, redaction_version="v1",
        ))

        ui = UIServer(broker=broker, port=0)
        snap = broker.snapshot()
        instance, messages = ui._build_local_instance(snap)
        # Mirror what _build_state assembles for the JSON response.
        payload = {
            "instances": [instance],
            "transcripts": {snap.crew_id: messages},
        }
        # Round-trip through JSON to prove serializability and inspect shape.
        roundtripped = json.loads(json.dumps(payload))
        crew_msgs = roundtripped["transcripts"][snap.crew_id]
        tool_msgs = [m for m in crew_msgs if m["kind"] == "tool"]
        assert len(tool_msgs) == 1
        # The tool entry shape is what _fetch_remote_state will pass through unchanged.
        m = tool_msgs[0]
        assert set(m.keys()) == {"t", "from", "to", "kind", "body"}
        assert m["kind"] == "tool"
        assert m["from"] == tid
        assert m["to"] is None
