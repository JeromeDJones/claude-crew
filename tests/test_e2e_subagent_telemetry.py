"""E2E integration tests for Feature #7 (Subagent-Activity Envelopes).

Task 5 — 5 full-lifecycle scenarios covering:
  1. Single subagent happy path (SC-1 through SC-7)
  2. Parallel fan-out — two concurrent subagents (SC-11)
  3. Kill before PostToolUse — abandoned batch (SC-8, SC-9, SC-14)
  4. Kill with entry in scratch — tombstone runs _end_turn first (sentinel F1)
  5. Non-subagent tools isolated from subagent tracking (SC-10, SC-12)

Test structure mirrors test_e2e_tool_telemetry.py: real Broker + SdkTeammate
with SDK mocked (ProgrammableSDKClient). Hook callbacks are fired directly on
the teammate object — same technique used throughout the tool-telemetry tests.

Key notes:
  - Transcripts are disabled by conftest autouse; tests that assert transcript
    content use the enable_transcripts fixture to opt in.
  - Broker must be created AFTER enable_transcripts sets CLAUDE_CREW_TRANSCRIPT_DIR
    so the TranscriptSink picks up the tmp_path directory.
  - _record_task_notif() is called directly to simulate the TNM wiring that
    _collect_response_text() performs during a live SDK turn.
  - _end_turn() is called directly to trigger subagent_result emission from
    _closed_subagent_scratch (same as SdkTeammate._handle_one_turn's finally block).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker, LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.sdk_teammate import SdkTeammate, _ClosedSubagentEntry, _SubagentUseEntry
from tests.fakes.programmable_sdk_client import ProgrammableSDKClient
from tests.fakes.sdk import task_notification


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _read_transcript_lines(tmp_path: Path) -> list[dict]:
    """Read all JSONL lines from the single transcript file in tmp_path."""
    files = list(tmp_path.iterdir())
    assert files, "no transcript file found in tmp_path"
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


def _patch_sdk(monkeypatch, fake: ProgrammableSDKClient) -> None:
    """Monkeypatch ClaudeSDKClient to return fake for every construction."""
    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)


def _factory_for(fake: ProgrammableSDKClient):
    """Return a teammate factory that creates SdkTeammate with SDK mocked externally."""
    def _factory(id: str, name: str, role: str, **_kw: Any) -> SdkTeammate:
        return SdkTeammate(id=id, name=name, role=role)
    return _factory


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


@pytest.fixture
def enable_transcripts(monkeypatch, tmp_path):
    """Override conftest default: enable JSONL sink, redirect to tmp_path."""
    monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
    return tmp_path


# ─────────────────────────────────────────────────────────────────────────────
# F7 Subagent Telemetry E2E Scenarios
# ─────────────────────────────────────────────────────────────────────────────


class TestSubagentTelemetryE2E:
    """Full-stack scenarios for Feature #7 subagent-activity envelopes.

    All tests spin a real Broker + SdkTeammate with the SDK mocked. Hook
    callbacks are fired directly on the teammate object — same technique used
    throughout test_sdk_teammate.py and test_e2e_tool_telemetry.py.
    """

    # ── Scenario 1: Single subagent — full lifecycle (SC-1 through SC-7) ─────

    async def test_single_subagent_full_lifecycle(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-1 through SC-7: PreToolUse → TNM → PostToolUse → _end_turn.

        Verifies:
          - subagent_spawn emitted at PreToolUse time (BEFORE subagent_result)
          - subagent_result emitted at _end_turn with outcome=ok, correct summary,
            tnm_missing=False
          - current_subagents is empty after _end_turn (both dicts cleared)
          - last_subagent_completed reflects the completed subagent
          - _tool_uses is empty (SC-12: no contamination)
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        # Broker created after enable_transcripts sets CLAUDE_CREW_TRANSCRIPT_DIR.
        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            tu_id = "tu-sc1-lifecycle"
            agent_id = "agent-abc123"

            # Step 1: PreToolUse fires — entry goes to _subagent_uses, spawn emitted.
            await teammate._on_pre_tool_use({"agent_id": agent_id}, tu_id, {})
            assert tu_id in teammate._subagent_uses, "PreToolUse must populate _subagent_uses"

            # Step 2: TNM arrives (simulates _collect_response_text wiring).
            tnm = task_notification(status="completed", summary="Done.")
            teammate._record_task_notif(tu_id, tnm)

            # Step 3: PostToolUse fires — entry moves to _closed_subagent_scratch.
            await teammate._on_post_tool_use({"agent_id": agent_id}, tu_id, {})
            assert tu_id not in teammate._subagent_uses, "PostToolUse must pop from _subagent_uses"
            assert tu_id in teammate._closed_subagent_scratch, "PostToolUse must populate scratch"

            # SC-12: subagent events must never touch _tool_uses.
            assert teammate._tool_uses == {}, "subagent path must not contaminate _tool_uses"

            # D10: current_subagents merges both dicts — scratch entry still visible.
            snap_mid = teammate.status_snapshot()
            assert any(e["tool_use_id"] == tu_id for e in snap_mid["current_subagents"]), (
                "scratch entry must appear in current_subagents until _end_turn"
            )

            # Step 4: _end_turn emits subagent_result and clears both scratch dicts.
            teammate._end_turn()

            # After _end_turn: both dicts cleared, current_subagents is empty.
            snap_post = teammate.status_snapshot()
            assert snap_post["current_subagents"] == [], (
                "current_subagents must be empty after _end_turn"
            )
            assert snap_post["last_subagent_completed"] is not None, (
                "last_subagent_completed must be set after successful _end_turn"
            )
            last = snap_post["last_subagent_completed"]
            assert last["outcome"] == "ok"
            assert last["summary"] == "Done."
            assert last["tool_use_id"] == tu_id

            # get_teammate_status (alive path) reflects the same.
            status = b.get_teammate_status(tid)
            assert status["alive"] is True
            assert status["current_subagents"] == []
            assert status["last_subagent_completed"] is not None
            assert status["last_subagent_completed"]["outcome"] == "ok"

        finally:
            await b.shutdown_all()

        # Transcript assertions (read after sink is closed by shutdown_all).
        lines = _read_transcript_lines(enable_transcripts)
        spawn_lines = [l for l in lines if l.get("kind") == "subagent_spawn"]
        result_lines = [l for l in lines if l.get("kind") == "subagent_result"]

        assert spawn_lines, f"expected at least one subagent_spawn; transcript: {lines}"
        assert result_lines, f"expected at least one subagent_result; transcript: {lines}"

        spawn = next((l for l in spawn_lines if l.get("tool_use_id") == tu_id), None)
        result = next((l for l in result_lines if l.get("tool_use_id") == tu_id), None)
        assert spawn is not None, f"no subagent_spawn for {tu_id!r}; lines: {spawn_lines}"
        assert result is not None, f"no subagent_result for {tu_id!r}; lines: {result_lines}"

        assert spawn["agent_id"] == agent_id
        assert result["outcome"] == "ok"
        assert result["summary"] == "Done."
        assert result["tnm_missing"] is False

        # Ordering: spawn must precede result.
        spawn_idx = next(i for i, l in enumerate(lines) if l.get("kind") == "subagent_spawn" and l.get("tool_use_id") == tu_id)
        result_idx = next(i for i, l in enumerate(lines) if l.get("kind") == "subagent_result" and l.get("tool_use_id") == tu_id)
        assert spawn_idx < result_idx, (
            f"subagent_spawn (line {spawn_idx}) must precede subagent_result (line {result_idx})"
        )

    # ── Scenario 2: Parallel fan-out — two concurrent subagents (SC-11) ─────

    async def test_parallel_fanout_two_concurrent_subagents(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-11: two concurrent subagents both tracked, both emitted at _end_turn.

        Verifies:
          - Both entries appear in current_subagents while in flight
          - Two subagent_result records in transcript, one per tool_use_id
          - Each result has correct outcome and summary
          - last_subagent_completed is one of the two (last PostToolUse wins)
          - current_subagents is empty after _end_turn
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            tu1, tu2 = "tu-parallel-sub-1", "tu-parallel-sub-2"
            agent1, agent2 = "agent-fanout-1", "agent-fanout-2"

            # Both PreToolUse events (parallel spawn).
            await teammate._on_pre_tool_use({"agent_id": agent1}, tu1, {})
            await teammate._on_pre_tool_use({"agent_id": agent2}, tu2, {})

            # Both in-flight simultaneously.
            snap_mid = teammate.status_snapshot()
            assert len(snap_mid["current_subagents"]) == 2, (
                f"expected 2 in-flight subagents; got {snap_mid['current_subagents']}"
            )
            in_flight_ids = {e["tool_use_id"] for e in snap_mid["current_subagents"]}
            assert tu1 in in_flight_ids
            assert tu2 in in_flight_ids

            # TNMs for both.
            tnm1 = task_notification(status="completed", summary="Agent-1 done")
            tnm2 = task_notification(status="completed", summary="Agent-2 done")
            teammate._record_task_notif(tu1, tnm1)
            teammate._record_task_notif(tu2, tnm2)

            # PostToolUse for both (moves both to scratch).
            await teammate._on_post_tool_use({"agent_id": agent1}, tu1, {})
            await teammate._on_post_tool_use({"agent_id": agent2}, tu2, {})

            assert teammate._subagent_uses == {}, "both must be popped from _subagent_uses"
            assert len(teammate._closed_subagent_scratch) == 2, "both must be in scratch"

            # _end_turn emits two subagent_result records.
            teammate._end_turn()

            snap_post = teammate.status_snapshot()
            assert snap_post["current_subagents"] == [], "both subagents must be cleared after _end_turn"

            # last_subagent_completed is one of the two (whichever _end_turn processed last).
            last = snap_post["last_subagent_completed"]
            assert last is not None
            assert last["tool_use_id"] in (tu1, tu2)
            assert last["outcome"] == "ok"

        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(enable_transcripts)
        result_lines = [l for l in lines if l.get("kind") == "subagent_result"]

        assert len(result_lines) >= 2, (
            f"expected at least 2 subagent_result records; got {result_lines}"
        )

        result_by_tuid = {l["tool_use_id"]: l for l in result_lines if l.get("tool_use_id") in (tu1, tu2)}
        assert tu1 in result_by_tuid, f"missing subagent_result for {tu1!r}"
        assert tu2 in result_by_tuid, f"missing subagent_result for {tu2!r}"

        r1 = result_by_tuid[tu1]
        r2 = result_by_tuid[tu2]
        assert r1["outcome"] == "ok"
        assert r1["summary"] == "Agent-1 done"
        assert r1["tnm_missing"] is False
        assert r2["outcome"] == "ok"
        assert r2["summary"] == "Agent-2 done"
        assert r2["tnm_missing"] is False

        # Dead-path status (after shutdown_all kill).
        status = b.get_teammate_status(tid)
        assert status["current_subagents"] == []

    # ── Scenario 3: Kill before PostToolUse — abandoned batch ────────────────

    async def test_kill_before_post_tool_use_emits_abandoned_batch(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-8/SC-9/SC-14: entry in _subagent_uses when killed → subagent_abandoned_batch.

        When a teammate is killed while a subagent Pre has fired but Post has not:
          - _end_turn(close_tools=False) runs: _closed_subagent_scratch is empty,
            so no subagent_result is emitted
          - _close_open_subagents("kill") drains _subagent_uses: emits
            subagent_abandoned_batch
          - in_flight_subagents_at_death == 1 (captured before _close_open_subagents)
          - current_subagents == [] in dead status
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            tu_id = "tu-kill-before-post"
            agent_id = "agent-kill-victim"

            # Fire PreToolUse (entry in _subagent_uses).
            await teammate._on_pre_tool_use({"agent_id": agent_id}, tu_id, {})
            assert tu_id in teammate._subagent_uses, "entry must be in _subagent_uses before kill"

            # Kill before PostToolUse.
            await b.kill_teammate(tid)
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(enable_transcripts)
        spawn_lines = [l for l in lines if l.get("kind") == "subagent_spawn"]
        result_lines = [l for l in lines if l.get("kind") == "subagent_result"]
        abandoned_lines = [l for l in lines if l.get("kind") == "subagent_abandoned_batch"]

        assert spawn_lines, f"expected subagent_spawn; transcript: {lines}"
        assert abandoned_lines, f"expected subagent_abandoned_batch; transcript: {lines}"
        assert result_lines == [], (
            f"expected no subagent_result when killed before Post; got: {result_lines}"
        )

        # Spawn before abandoned.
        spawn_idx = next(i for i, l in enumerate(lines) if l.get("kind") == "subagent_spawn")
        abandoned_idx = next(i for i, l in enumerate(lines) if l.get("kind") == "subagent_abandoned_batch")
        assert spawn_idx < abandoned_idx, (
            f"subagent_spawn (line {spawn_idx}) must precede subagent_abandoned_batch (line {abandoned_idx})"
        )

        # Abandoned batch contains our tool_use_id.
        abandoned = abandoned_lines[0]
        tool_use_ids_in_batch = [s["tool_use_id"] for s in abandoned.get("subagents", [])]
        assert tu_id in tool_use_ids_in_batch, (
            f"expected {tu_id!r} in abandoned batch; got {tool_use_ids_in_batch}"
        )

        # Post-mortem status.
        status = b.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["current_subagents"] == []
        assert status["in_flight_subagents_at_death"] == 1, (
            f"expected in_flight_subagents_at_death=1; got {status['in_flight_subagents_at_death']}"
        )

    # ── Scenario 4: Kill with scratch entry — tombstone drains scratch via _end_turn

    async def test_kill_with_scratch_entry_emits_result_from_end_turn(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """Sentinel F1: when PostToolUse fired but _end_turn hasn't, killing runs
        _tombstone_teammate which calls _end_turn(close_tools=False) as step 2.
        This processes _closed_subagent_scratch and emits subagent_result.
        _close_open_subagents (step 8b) then finds _subagent_uses empty → no
        additional abandoned_batch.

        Verifies:
          - subagent_spawn is in transcript
          - subagent_result is emitted (from _end_turn in tombstone, not _close_open_subagents)
          - tnm_missing=True (no TNM recorded before kill)
          - outcome defaults to hook_outcome="ok" (PostToolUse path)
          - no subagent_abandoned_batch (scratch was drained by _end_turn)
          - in_flight_subagents_at_death == 0 (scratch cleared before count is taken at step 4)
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            tu_id = "tu-scratch-kill"
            agent_id = "agent-scratch-victim"

            # Step 1: PreToolUse (entry in _subagent_uses).
            await teammate._on_pre_tool_use({"agent_id": agent_id}, tu_id, {})
            assert tu_id in teammate._subagent_uses

            # Step 2: PostToolUse (moves entry to _closed_subagent_scratch).
            # No TNM recorded — simulates kill-before-TNM-arrives.
            await teammate._on_post_tool_use({"agent_id": agent_id}, tu_id, {})
            assert tu_id not in teammate._subagent_uses, "PostToolUse must pop from _subagent_uses"
            assert tu_id in teammate._closed_subagent_scratch, "PostToolUse must populate scratch"

            # Step 3: Kill before _end_turn runs.
            # _tombstone_teammate step 2: _end_turn(close_tools=False) → processes scratch
            # _tombstone_teammate step 4: snapshot (scratch already cleared) → in_flight = 0
            # _tombstone_teammate step 8b: _close_open_subagents → _subagent_uses empty → no-op
            await b.kill_teammate(tid)
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(enable_transcripts)
        spawn_lines = [l for l in lines if l.get("kind") == "subagent_spawn"]
        result_lines = [l for l in lines if l.get("kind") == "subagent_result"]
        abandoned_lines = [l for l in lines if l.get("kind") == "subagent_abandoned_batch"]

        # spawn must be present.
        assert spawn_lines, f"expected subagent_spawn; transcript: {lines}"

        # _end_turn(close_tools=False) processes scratch → emits subagent_result.
        assert result_lines, (
            f"expected subagent_result from _end_turn(close_tools=False); transcript: {lines}"
        )
        result = next(
            (l for l in result_lines if l.get("tool_use_id") == tu_id), None
        )
        assert result is not None, f"no subagent_result for {tu_id!r}; result_lines: {result_lines}"
        # No TNM was recorded before kill — tnm_missing=True, outcome from hook_outcome.
        assert result["tnm_missing"] is True, (
            f"expected tnm_missing=True (no TNM before kill); got: {result}"
        )
        assert result["outcome"] == "ok", (
            f"expected outcome=ok (hook_outcome from PostToolUse); got: {result}"
        )

        # _close_open_subagents found _subagent_uses empty → no abandoned_batch.
        assert abandoned_lines == [], (
            f"expected no abandoned_batch (scratch was drained by _end_turn first); "
            f"got: {abandoned_lines}"
        )

        # Post-mortem: in_flight count was 0 (scratch cleared before step 4 snapshot).
        status = b.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["current_subagents"] == []
        assert status["in_flight_subagents_at_death"] == 0, (
            f"scratch was cleared by _end_turn before count snapshot; "
            f"expected 0; got {status['in_flight_subagents_at_death']}"
        )

    # ── Scenario 5: Non-subagent tools isolated (SC-10, SC-12) ──────────────

    async def test_non_subagent_tools_isolated_from_subagent_tracking(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-10/SC-12: Bash tool goes through _tool_uses; Task subagent goes
        through _subagent_uses. Neither pollutes the other's tracking namespace.

        Verifies:
          - Bash emits tool_start / tool_end
          - Task subagent emits subagent_spawn / subagent_result
          - last_tool_completed reflects Bash only (subagent Post doesn't set it)
          - _tool_uses remains empty after subagent Pre (SC-12)
          - current_tools and current_subagents both empty after _end_turn
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            bash_tuid = "tu-bash-isolation"
            task_tuid = "tu-task-isolation"
            agent_id = "agent-isolation-sub"

            # 1. Bash PreToolUse (no agent_id → goes to _tool_uses).
            await teammate._on_pre_tool_use(
                {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "echo hi"}},
                bash_tuid, {},
            )
            # SC-12: subagent dicts must not be touched by non-subagent Pre.
            assert teammate._subagent_uses == {}, "Bash Pre must not touch _subagent_uses"
            assert teammate._closed_subagent_scratch == {}, "Bash Pre must not touch scratch"
            assert bash_tuid in teammate._tool_uses, "Bash Pre must populate _tool_uses"

            # 2. Bash PostToolUse (clears _tool_uses entry, sets _last_tool_completed).
            await teammate._on_post_tool_use(
                {"agent_id": None, "tool_name": "Bash", "tool_response": "hi"},
                bash_tuid, {},
            )
            assert bash_tuid not in teammate._tool_uses, "Bash Post must pop from _tool_uses"
            assert teammate._last_tool_completed is not None
            assert teammate._last_tool_completed["tool_name"] == "Bash"

            # 3. Task subagent PreToolUse (agent_id set → goes to _subagent_uses).
            await teammate._on_pre_tool_use({"agent_id": agent_id}, task_tuid, {})
            # SC-10: _tool_uses unaffected by subagent Pre.
            assert task_tuid not in teammate._tool_uses, (
                "subagent Pre must not contaminate _tool_uses"
            )
            assert task_tuid in teammate._subagent_uses, "subagent Pre must populate _subagent_uses"

            # 4. TNM for Task subagent.
            tnm = task_notification(status="completed", summary="Task done")
            teammate._record_task_notif(task_tuid, tnm)

            # 5. Task PostToolUse (moves to scratch; does NOT update _last_tool_completed).
            await teammate._on_post_tool_use({"agent_id": agent_id}, task_tuid, {})
            # _last_tool_completed still reflects Bash (subagent Post doesn't update it).
            assert teammate._last_tool_completed["tool_name"] == "Bash", (
                "subagent Post must not overwrite _last_tool_completed"
            )

            # 6. _end_turn (emits subagent_result, clears scratch).
            teammate._end_turn()

            # After turn: both namespaces empty.
            snap_post = teammate.status_snapshot()
            assert snap_post["current_tools"] == [], "current_tools must be empty after _end_turn"
            assert snap_post["current_subagents"] == [], "current_subagents must be empty after _end_turn"

            # last_tool_completed still Bash; last_subagent_completed now Task.
            assert snap_post["last_tool_completed"]["tool_name"] == "Bash"
            assert snap_post["last_subagent_completed"] is not None
            assert snap_post["last_subagent_completed"]["tool_use_id"] == task_tuid

        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(enable_transcripts)

        # Bash emits tool_start + tool_end.
        tool_start_lines = [l for l in lines if l.get("kind") == "tool_start"]
        tool_end_lines = [l for l in lines if l.get("kind") == "tool_end"]
        bash_start = next((l for l in tool_start_lines if l.get("tool_use_id") == bash_tuid), None)
        bash_end = next((l for l in tool_end_lines if l.get("tool_use_id") == bash_tuid), None)
        assert bash_start is not None, f"missing tool_start for Bash; tool_starts: {tool_start_lines}"
        assert bash_end is not None, f"missing tool_end for Bash; tool_ends: {tool_end_lines}"
        assert bash_start["tool_name"] == "Bash"
        assert bash_end["outcome"] == "ok"

        # Task subagent emits subagent_spawn + subagent_result.
        spawn_lines = [l for l in lines if l.get("kind") == "subagent_spawn"]
        result_lines = [l for l in lines if l.get("kind") == "subagent_result"]
        task_spawn = next((l for l in spawn_lines if l.get("tool_use_id") == task_tuid), None)
        task_result = next((l for l in result_lines if l.get("tool_use_id") == task_tuid), None)
        assert task_spawn is not None, f"missing subagent_spawn for Task; spawns: {spawn_lines}"
        assert task_result is not None, f"missing subagent_result for Task; results: {result_lines}"
        assert task_result["outcome"] == "ok"
        assert task_result["summary"] == "Task done"

        # SC-10: Bash tool_use_id must NOT appear in subagent records.
        all_subagent_tuids = {l.get("tool_use_id") for l in spawn_lines + result_lines}
        assert bash_tuid not in all_subagent_tuids, (
            f"Bash tool_use_id must not appear in subagent records; found in: {all_subagent_tuids}"
        )

        # SC-12: Task tool_use_id must NOT appear in tool_start/tool_end records.
        all_tool_tuids = {l.get("tool_use_id") for l in tool_start_lines + tool_end_lines}
        assert task_tuid not in all_tool_tuids, (
            f"Task subagent tool_use_id must not appear in tool records; found in: {all_tool_tuids}"
        )

        # Post-mortem (dead) status.
        status = b.get_teammate_status(tid)
        assert status["current_tools"] == []
        assert status["current_subagents"] == []
        assert status["last_tool_completed"] is not None
        assert status["last_tool_completed"]["tool_name"] == "Bash"
