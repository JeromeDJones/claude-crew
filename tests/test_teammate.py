"""Unit tests for Teammate base class F8 additions (Task 2).

BDD scenarios covered:
- _close_open_tools emits tool_end for each in-flight entry (SC-14)
- _close_open_tools with reason="kill" emits outcome="killed" (SC-14)
- _close_open_tools is iteration-safe under mid-iteration mutation (sentinel A2)
- _close_open_tools clears _tool_uses even if write_tool_event raises (D9)
- _close_open_tools with reason="death" emits outcome="abandoned"
- status_snapshot reports empty current_tools when no Pre fired (SC-9)
- status_snapshot reports last-started semantics for current_tool (SC-9)
- status_snapshot includes redaction_version (D11)
- status_snapshot last_tool_completed is None initially
"""

from __future__ import annotations

import collections
import time
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from claude_crew.teammate import StubTeammate, _ToolUseEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_teammate_with_mock_broker() -> StubTeammate:
    """Return a StubTeammate with a mock broker._sink injected."""
    teammate = StubTeammate(id="t-test", name="tester", role="worker")
    mock_broker = MagicMock()
    mock_sink = MagicMock()
    mock_broker._sink = mock_sink
    teammate._broker = mock_broker
    return teammate


def _add_tool(
    teammate: StubTeammate,
    tool_name: str,
    tool_use_id: str,
    started_ago: float = 0.0,
    args_summary: str | None = None,
) -> _ToolUseEntry:
    """Pre-populate a _ToolUseEntry into teammate._tool_uses."""
    entry = _ToolUseEntry(
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        started_at_wallclock=time.time() - started_ago,
        args_summary=args_summary,
    )
    teammate._tool_uses[tool_use_id] = entry
    return entry


# ---------------------------------------------------------------------------
# _close_open_tools scenarios
# ---------------------------------------------------------------------------


class TestCloseOpenTools:
    def test_emits_tool_end_for_each_in_flight_entry(self) -> None:
        """SC-14: two in-flight tools → two tool_end lines, both abandoned."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-1", started_ago=5.0)
        _add_tool(teammate, "Task", "tu-2", started_ago=5.0)

        teammate._close_open_tools(reason="turn_end")

        sink = teammate._broker._sink
        assert sink.write_tool_event.call_count == 2

        written: dict[str, dict[str, Any]] = {}
        for c in sink.write_tool_event.call_args_list:
            event_kind, fields = c.args
            written[fields["tool_use_id"]] = {"kind": event_kind, **fields}

        assert set(written) == {"tu-1", "tu-2"}
        for tu_id, rec in written.items():
            assert rec["kind"] == "tool_end"
            assert rec["outcome"] == "abandoned"
            assert rec["duration_seconds"] >= 4.9, (
                f"duration_seconds for {tu_id} should be ≥4.9s, got {rec['duration_seconds']}"
            )
            assert "tool was in flight when turn_end closed it" in rec["error_summary"]

        assert teammate._tool_uses == {}
        assert "tu-1" in teammate._recently_closed_tool_use_ids
        assert "tu-2" in teammate._recently_closed_tool_use_ids

    def test_reason_kill_emits_outcome_killed(self) -> None:
        """SC-14: reason='kill' → outcome='killed' in tool_end record."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-1")

        teammate._close_open_tools(reason="kill")

        _, fields = teammate._broker._sink.write_tool_event.call_args.args
        assert fields["outcome"] == "killed"
        assert "kill" in fields["error_summary"]

    def test_reason_death_emits_outcome_abandoned(self) -> None:
        """SC-14: reason='death' → outcome='abandoned' (same as turn_end)."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "WebFetch", "tu-1")

        teammate._close_open_tools(reason="death")

        _, fields = teammate._broker._sink.write_tool_event.call_args.args
        assert fields["outcome"] == "abandoned"
        assert "death" in fields["error_summary"]

    def test_iteration_safe_under_mid_iteration_mutation(self) -> None:
        """Sentinel A2: snapshot-first protects iteration from mid-loop dict mutation."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-1")
        _add_tool(teammate, "Task", "tu-2")
        _add_tool(teammate, "WebFetch", "tu-3")

        write_calls: list[str] = []

        def spy_write(event_kind: str, fields: dict) -> None:
            write_calls.append(fields["tool_use_id"])
            # Simulate mid-iteration mutation — clear the live dict.
            # The snapshot-first approach means iteration continues over
            # the original list(items()) snapshot regardless.
            teammate._tool_uses.clear()

        teammate._broker._sink.write_tool_event.side_effect = spy_write

        teammate._close_open_tools(reason="turn_end")

        # All three entries were processed despite mid-iteration dict.clear().
        assert len(write_calls) == 3
        assert set(write_calls) == {"tu-1", "tu-2", "tu-3"}
        assert teammate._tool_uses == {}

    def test_clears_tool_uses_even_if_write_tool_event_raises(self) -> None:
        """D9: finally block guarantees _tool_uses is empty even if write raises."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-1")

        teammate._broker._sink.write_tool_event.side_effect = OSError("disk full")

        # Must NOT propagate the exception.
        teammate._close_open_tools(reason="turn_end")

        assert teammate._tool_uses == {}

    def test_no_entries_is_a_no_op(self) -> None:
        """Empty _tool_uses → zero writes, dict still empty."""
        teammate = _make_teammate_with_mock_broker()
        assert teammate._tool_uses == {}

        teammate._close_open_tools(reason="turn_end")

        teammate._broker._sink.write_tool_event.assert_not_called()
        assert teammate._tool_uses == {}

    def test_tool_use_ids_added_to_recently_closed_deque(self) -> None:
        """D8: closed tool_use_ids land in _recently_closed_tool_use_ids for late-Post dedup."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-abc")

        teammate._close_open_tools(reason="kill")

        assert "tu-abc" in teammate._recently_closed_tool_use_ids


# ---------------------------------------------------------------------------
# status_snapshot F8 field scenarios
# ---------------------------------------------------------------------------


class TestStatusSnapshotF8Fields:
    def test_empty_current_tools_when_no_tools_active(self) -> None:
        """BDD: status_snapshot returns empty current_tools list when _tool_uses is empty."""
        teammate = StubTeammate(id="t-x", name="x", role="r")

        snap = teammate.status_snapshot()

        assert snap["current_tools"] == []
        assert snap["current_tool"] is None
        assert snap["current_tool_count"] == 0

    def test_last_started_semantics_for_current_tool(self) -> None:
        """SC-9: current_tool is the tool with the highest started_at_wallclock."""
        teammate = StubTeammate(id="t-x", name="x", role="r")

        # A started earlier, B started later.
        entry_a = _ToolUseEntry(
            tool_name="Bash",
            tool_use_id="tu-a",
            started_at_wallclock=10.0,
            args_summary=None,
        )
        entry_b = _ToolUseEntry(
            tool_name="Task",
            tool_use_id="tu-b",
            started_at_wallclock=15.0,
            args_summary=None,
        )
        # Insert in reverse order to prove sorting, not insertion order.
        teammate._tool_uses["tu-b"] = entry_b
        teammate._tool_uses["tu-a"] = entry_a

        snap = teammate.status_snapshot()

        assert snap["current_tool"] == "Task", (
            "last-started tool (tu-b, t=15) should be current_tool"
        )
        assert snap["current_tool_count"] == 2
        tools = snap["current_tools"]
        assert len(tools) == 2
        assert tools[0]["tool_name"] == "Bash", "Bash (t=10) should sort first"
        assert tools[-1]["tool_name"] == "Task", "Task (t=15) should sort last"

    def test_includes_redaction_version(self) -> None:
        """D11: status_snapshot always carries redaction_version."""
        teammate = StubTeammate(id="t-x", name="x", role="r")

        snap = teammate.status_snapshot()

        assert "redaction_version" in snap
        assert snap["redaction_version"] == "v1"

    def test_last_tool_completed_initially_none(self) -> None:
        """last_tool_completed is None before any Post hook fires (T3 populates it)."""
        teammate = StubTeammate(id="t-x", name="x", role="r")

        snap = teammate.status_snapshot()

        assert snap["last_tool_completed"] is None

    def test_f6_fields_still_present(self) -> None:
        """SC-11: F6 telemetry fields are preserved verbatim (non-regression)."""
        teammate = StubTeammate(id="t-x", name="x", role="r")

        snap = teammate.status_snapshot()

        assert "last_activity_at_wallclock" in snap
        assert "current_turn_started_at_wallclock" in snap
        assert "idle_seconds" in snap

    def test_current_tools_shape(self) -> None:
        """Each entry in current_tools has the four expected keys."""
        teammate = StubTeammate(id="t-x", name="x", role="r")
        _add_tool(teammate, "WebFetch", "tu-wf", args_summary="url=https://example.com")

        snap = teammate.status_snapshot()

        assert len(snap["current_tools"]) == 1
        item = snap["current_tools"][0]
        assert set(item.keys()) == {"tool_name", "tool_use_id", "started_at_wallclock", "args_summary"}
        assert item["tool_name"] == "WebFetch"
        assert item["tool_use_id"] == "tu-wf"
        assert item["args_summary"] == "url=https://example.com"


# ---------------------------------------------------------------------------
# Initialization checks
# ---------------------------------------------------------------------------


class TestToolTrackingInit:
    def test_stub_teammate_initializes_tool_fields(self) -> None:
        """StubTeammate.__init__ must initialize all three F8 fields."""
        t = StubTeammate(id="t-1", name="n", role="r")

        assert t._tool_uses == {}
        assert isinstance(t._recently_closed_tool_use_ids, collections.deque)
        assert t._recently_closed_tool_use_ids.maxlen == 64
        assert t._last_tool_completed is None

    def test_recently_closed_deque_maxlen(self) -> None:
        """Deque enforces maxlen=64 to bound memory on long-running teammates."""
        t = StubTeammate(id="t-1", name="n", role="r")

        for i in range(100):
            t._recently_closed_tool_use_ids.append(f"tu-{i}")

        assert len(t._recently_closed_tool_use_ids) == 64
        # Latest 64 are retained; earliest are evicted.
        assert "tu-99" in t._recently_closed_tool_use_ids
        assert "tu-0" not in t._recently_closed_tool_use_ids


# ---------------------------------------------------------------------------
# _end_turn scenarios (sentinel inner-4 fix-now #1)
# ---------------------------------------------------------------------------


class TestEndTurnAbandonsTools:
    """SC-14 coverage of the turn_end abandonment lifecycle.

    A turn that ends with tools still in `_tool_uses` (SDK-quirk
    dropped Post) emits `tool_end(outcome="abandoned")` per still-open
    tool. Without this, phantom in-flight tools would bleed into the
    next turn's status payload.
    """

    def test_end_turn_abandons_open_tools(self) -> None:
        """_end_turn() with default close_tools=True closes open tools."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-orphan", started_ago=2.0)

        teammate._end_turn()

        # Tool record was emitted with outcome="abandoned"
        sink = teammate._broker._sink
        assert sink.write_tool_event.call_count == 1
        kind, fields = sink.write_tool_event.call_args[0]
        assert kind == "tool_end"
        assert fields["outcome"] == "abandoned"
        assert fields["tool_use_id"] == "tu-orphan"
        # Dict cleared
        assert teammate._tool_uses == {}
        # tool_use_id added to recently-closed for late-Post dedup
        assert "tu-orphan" in teammate._recently_closed_tool_use_ids
        # turn_started cleared
        assert teammate._current_turn_started_at_wallclock is None

    def test_end_turn_with_close_tools_false_skips_close(self) -> None:
        """Broker death/kill path uses close_tools=False to defer closure."""
        teammate = _make_teammate_with_mock_broker()
        _add_tool(teammate, "Bash", "tu-still-open", started_ago=2.0)

        teammate._end_turn(close_tools=False)

        # No tool_end emitted; dict still populated for the broker's
        # _close_open_tools(reason="death"|"kill") to handle.
        sink = teammate._broker._sink
        assert sink.write_tool_event.call_count == 0
        assert "tu-still-open" in teammate._tool_uses
        # turn_started still cleared (the F6 contract end_turn always honored)
        assert teammate._current_turn_started_at_wallclock is None

    def test_end_turn_with_no_open_tools_is_noop_for_close(self) -> None:
        """Empty _tool_uses → no transcript line, no error."""
        teammate = _make_teammate_with_mock_broker()

        teammate._end_turn()

        sink = teammate._broker._sink
        assert sink.write_tool_event.call_count == 0
        assert teammate._current_turn_started_at_wallclock is None


# ---------------------------------------------------------------------------
# _get_redaction_version removal and REDACTION_VERSION replacement
# ---------------------------------------------------------------------------


class TestRedactionVersionCleanup:
    def test_get_redaction_version_function_removed(self) -> None:
        """T4: _get_redaction_version should be removed; REDACTION_VERSION is imported directly."""
        import claude_crew.teammate as tm

        assert not hasattr(tm, "_get_redaction_version"), (
            "_get_redaction_version should be removed; use REDACTION_VERSION from claude_crew.redaction directly"
        )

    def test_redaction_version_still_in_status_snapshot(self) -> None:
        """T4: status_snapshot still carries redaction_version == 'v1' after the cleanup."""
        teammate = StubTeammate(id="t-x", name="x", role="r")

        snap = teammate.status_snapshot()

        assert snap["redaction_version"] == "v1"
