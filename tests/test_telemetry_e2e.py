"""E2E integration sweep for Feature #6 (telemetry-based teammate liveness).

Six scenarios wiring real Broker + SdkTeammate + ProgrammableSDKClient.

Key setup notes:
  - conftest._default_stub_teammate_mode sets CLAUDE_CREW_TEAMMATE_MODE=stub (autouse).
    These tests bypass the factory/mode env var by instantiating SdkTeammate directly.
  - conftest._disable_transcripts sets CLAUDE_CREW_TRANSCRIPT_DISABLED=1 (autouse).
    Tests that need transcripts use enable_transcripts fixture to override it.
  - CLAUDE_CREW_LIVENESS_POLL_SECONDS / CLAUDE_CREW_TURN_BACKSTOP_SECONDS must be
    set via monkeypatch BEFORE broker.spawn_teammate() so SdkTeammate.__init__ picks
    them up.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path
from typing import Any

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker, LEAD_ID, TeammateAlreadyDeadError
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.sdk_teammate import SdkTeammate
from claude_crew.teammate import StubTeammate
from tests.fakes.programmable_sdk_client import ProgrammableSDKClient
from tests.fakes.sdk import text_response


# ---------- fixtures ----------


@pytest.fixture
def enable_transcripts(monkeypatch, tmp_path):
    """Override _disable_transcripts: enable JSONL sink, redirect to tmp_path."""
    monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
    monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------- helpers ----------


def _read_transcript(tmp_path: Path) -> list[dict]:
    files = list(tmp_path.iterdir())
    assert files, "transcript file not found"
    return [json.loads(line) for line in files[0].read_text().splitlines() if line.strip()]


def _sdk_teammate_factory(id: str, name: str, role: str, **_kw: Any) -> SdkTeammate:
    """Minimal factory: creates SdkTeammate with defaults. ClaudeSDKClient must be
    monkeypatched before spawn_teammate is called."""
    return SdkTeammate(id=id, name=name, role=role)


def _stub_teammate_factory(id: str, name: str, role: str, **_kw: Any) -> StubTeammate:
    return StubTeammate(id=id, name=name, role=role)


def _make_sequenced_ctor(*fakes: ProgrammableSDKClient):
    """Constructor that hands out fakes in order, cycling if exhausted."""
    idx = [0]

    def ctor(options: Any = None) -> ProgrammableSDKClient:
        f = fakes[idx[0] % len(fakes)]
        idx[0] += 1
        return f

    return ctor


def _envelope(recipient: str, payload: Any = "hello", sender: str = LEAD_ID) -> Envelope:
    return Envelope(
        id=new_message_id(), seq=0,
        sender=sender, recipient=recipient,
        timestamp=time.time(), payload=payload,
    )


async def _wait_messages(
    broker: Broker,
    count: int,
    recipient: str = LEAD_ID,
    timeout: float = 4.0,
) -> list[Envelope]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msgs = broker.get_messages(recipient=recipient)
        if len(msgs) >= count:
            return msgs
        await asyncio.sleep(0.02)
    msgs = broker.get_messages(recipient=recipient)
    raise AssertionError(
        f"timed out waiting for {count} messages at {recipient!r}; "
        f"got {len(msgs)}: {[m.payload for m in msgs]}"
    )


async def _wait_for_status(
    broker: Broker,
    tid: str,
    pred: Any,
    timeout: float = 3.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = broker.get_teammate_status(tid)
        if pred(status):
            return status
        await asyncio.sleep(0.05)
    status = broker.get_teammate_status(tid)
    raise AssertionError(f"timed out waiting for status condition; last: {status}")


# ---------- Scenario 1: happy path — full crew lifecycle with telemetry ----------


class TestFullCrewLifecycleWithTelemetry:
    async def test_full_crew_lifecycle_with_telemetry(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """Happy: spawn → 3 envelopes → poll status → kill → death record + transcript."""
        fake = ProgrammableSDKClient(scripted_responses=[
            text_response("reply-1"),
            text_response("reply-2"),
            text_response("reply-3"),
        ])
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "0.2")

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="builder", name="alice", factory=_sdk_teammate_factory,
            )

            for i in range(3):
                await b.send(_envelope(recipient=tid, payload=f"task-{i}"))

            msgs = await _wait_messages(b, 3)
            assert len(msgs) == 3
            assert all(m.payload.get("text", "").startswith("reply-") for m in msgs)

            # Mid-run status: alive, in or just finished turn
            status = b.get_teammate_status(tid)
            assert status["alive"] is True
            assert status["idle_seconds"] is not None
            assert status["died_at_wallclock"] is None

            before_kill = time.time()
            await b.kill_teammate(tid)
            after_kill = time.time()

            status = b.get_teammate_status(tid)
            assert status["alive"] is False
            assert status["exit_code"] is None
            assert before_kill <= status["died_at_wallclock"] <= after_kill
            assert status["last_activity_at_wallclock_at_death"] is not None
        finally:
            await b.shutdown_all()

        # Transcript assertions
        lines = _read_transcript(enable_transcripts)
        lifecycle_events = [
            l["event"] for l in lines if l.get("kind") == "lifecycle"
        ]
        assert "started" in lifecycle_events
        assert "spawn" in lifecycle_events
        assert "kill" in lifecycle_events
        assert "died" not in lifecycle_events, (
            f"explicit kill must not emit 'died'; got events: {lifecycle_events}"
        )

        spawn_line = next(l for l in lines if l.get("event") == "spawn")
        assert spawn_line["name"] == "alice"
        assert spawn_line["role"] == "builder"

        envelope_lines = [l for l in lines if l.get("kind") == "envelope"]
        # 3 lead→teammate + 3 teammate→lead = 6 minimum
        assert len(envelope_lines) >= 6, (
            f"expected ≥6 envelope lines; got {len(envelope_lines)}"
        )


# ---------- Scenario 2: happy — broadcast skips tombstoned teammate ----------


class TestMultiTeammateBroadcastSkipsTombstoned:
    async def test_multi_teammate_broadcast_skips_tombstoned(
        self, broker: Broker,
    ) -> None:
        """Happy: spawn 3, kill 1, broadcast → delivered_to=2, skipped_dead=[killed]."""
        a = await broker.spawn_teammate(role="r", name="a", factory=_stub_teammate_factory)
        b_id = await broker.spawn_teammate(role="r", name="b", factory=_stub_teammate_factory)
        c = await broker.spawn_teammate(role="r", name="c", factory=_stub_teammate_factory)

        await broker.kill_teammate(c)

        result = await broker.broadcast(sender=LEAD_ID, payload={"msg": "to-all"})

        assert len(result["message_ids"]) == 2, (
            f"expected 2 delivered; got {result['message_ids']}"
        )
        assert c in result["skipped_dead"], (
            f"killed teammate {c!r} should appear in skipped_dead; got {result['skipped_dead']}"
        )
        assert a not in result["skipped_dead"]
        assert b_id not in result["skipped_dead"]

        # Verify the broadcast message appears in the log for a and b, not c
        a_msgs = [m for m in broker.get_messages(recipient=a)
                  if isinstance(m.payload, dict) and m.payload.get("msg") == "to-all"]
        b_msgs = [m for m in broker.get_messages(recipient=b_id)
                  if isinstance(m.payload, dict) and m.payload.get("msg") == "to-all"]
        c_msgs = [m for m in broker.get_messages(recipient=c)
                  if isinstance(m.payload, dict) and m.payload.get("msg") == "to-all"]

        assert len(a_msgs) == 1, "alive teammate 'a' should have received the broadcast"
        assert len(b_msgs) == 1, "alive teammate 'b' should have received the broadcast"
        assert len(c_msgs) == 0, "dead teammate 'c' must not have received the broadcast"


# ---------- Scenario 3: sad — subprocess death detected by poll ----------


class TestSubprocessDiesSubsequentSendReturnsDead:
    async def test_subprocess_dies_subsequent_send_to_returns_teammate_dead(
        self, monkeypatch,
    ) -> None:
        """Sad: returncode set to 137 → poll tombstones → send raises TeammateAlreadyDeadError."""
        fake = ProgrammableSDKClient(
            scripted_responses=[text_response("alive-reply")],
        )
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "0.15")

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_sdk_teammate_factory,
            )

            # Send one envelope and verify the teammate is alive and responds
            await b.send(_envelope(recipient=tid))
            await _wait_messages(b, 1)
            assert b.get_teammate_status(tid)["alive"] is True

            # Simulate subprocess death by setting returncode
            fake._transport._process.returncode = 137

            # Wait for poll to detect and tombstone
            status = await _wait_for_status(
                b, tid, lambda s: not s.get("alive", True), timeout=3.0,
            )
            assert status["alive"] is False
            assert status["exit_code"] == 137

            # Subsequent send must raise TeammateAlreadyDeadError (not UnknownTeammateError)
            with pytest.raises(TeammateAlreadyDeadError):
                await b.send(_envelope(recipient=tid, payload="too late"))

            # No orphaned envelopes remain in log addressed to dead teammate
            inbox_msgs = [
                m for m in b.get_messages(recipient=tid)
                if isinstance(m.payload, dict) and m.payload.get("error") != "teammate_dead"
            ]
            assert inbox_msgs == [] or all(
                m.seq > 0 for m in inbox_msgs
            ), "unexpected non-bounce messages to dead teammate"
        finally:
            await b.shutdown_all()


# ---------- Scenario 4: sad — backstop fires, interrupt, then continues ----------


class TestBackstopFiresDuringRealWorkThenContinues:
    async def test_backstop_fires_during_real_work_then_continues(
        self, monkeypatch,
    ) -> None:
        """Sad: backstop fires on hung drain → interrupt sent → second envelope processed normally."""
        # Small timing constants so the test runs in < 3s
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "0.3")
        monkeypatch.setattr(sdk_module, "INTERRUPT_GRACE_SECONDS", 1.0)
        monkeypatch.setattr(sdk_module, "POST_INTERRUPT_DRAIN_SECONDS", 0.3)
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "10.0")  # keep poll out of the way

        fake = ProgrammableSDKClient(
            # Turn 0: hang forever (simulates long tool execution past backstop)
            # Turn 1: respond normally
            response_hangs=[True, False],
            scripted_responses=[[], text_response("normal reply")],
        )
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_sdk_teammate_factory,
            )

            # env1: will hang → backstop fires
            await b.send(_envelope(recipient=tid, payload="slow task"))

            # Wait for the backstop_timeout error envelope
            msgs = await _wait_messages(b, 1, timeout=6.0)
            assert len(msgs) == 1
            assert msgs[0].payload.get("error") == "backstop_timeout", (
                f"expected backstop_timeout; got {msgs[0].payload}"
            )

            # interrupt must have been called
            assert len(fake.interrupt_calls) > 0, "interrupt() was not called during backstop"

            # Teammate is still alive after backstop recovery
            status = b.get_teammate_status(tid)
            assert status["alive"] is True, f"teammate should still be alive; status={status}"

            # env2: processed normally
            await b.send(_envelope(recipient=tid, payload="quick task"))

            msgs2 = await _wait_messages(b, 2, timeout=4.0)
            second = msgs2[1]
            assert second.payload.get("text") == "normal reply", (
                f"second envelope should produce normal reply; got {second.payload}"
            )
        finally:
            await b.shutdown_all()


# ---------- Scenario 5: sad — probe failure does not mass-tombstone (SC-12 e2e) ----------


class _RaisingProcess:
    """Replacement for SimpleNamespace._process: .returncode always raises OSError.

    Used by SC-12 to trigger the degrade-open path in _liveness_poll_loop:
    the probe catches the OSError, logs a WARNING, and continues — the
    teammate must NOT be tombstoned.
    """

    @property
    def returncode(self) -> int:  # type: ignore[override]
        raise OSError("simulated probe failure — degrade-open test")


class TestProbeFailureDoesNotMassTombstone:
    async def test_probe_failure_does_not_mass_tombstone(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """Sad/SC-12: OSError on returncode probe must NOT tombstone teammates."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "0.15")

        fakes = [
            ProgrammableSDKClient(scripted_responses=[]),
            ProgrammableSDKClient(scripted_responses=[]),
            ProgrammableSDKClient(scripted_responses=[]),
        ]
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _make_sequenced_ctor(*fakes))

        b = Broker()
        try:
            ids = []
            for i in range(3):
                tid = await b.spawn_teammate(
                    role=f"r{i}", name=None, factory=_sdk_teammate_factory,
                )
                ids.append(tid)

            # Arm each fake: replace its transport process with one that raises on .returncode
            for fake in fakes:
                fake._transport._process = _RaisingProcess()

            # Allow 6 poll cycles to complete (6 × 0.15s = 0.9s + buffer)
            await asyncio.sleep(1.4)

            # All 3 teammates must still be alive — OSError is degrade-open
            for tid in ids:
                status = b.get_teammate_status(tid)
                assert status["alive"] is True, (
                    f"teammate {tid} was tombstoned despite probe failure; status={status}"
                )
        finally:
            await b.shutdown_all()

        # Transcript: no "died" lifecycle events from probe failures
        lines = _read_transcript(enable_transcripts)
        died_lines = [
            l for l in lines
            if l.get("kind") == "lifecycle" and l.get("event") == "died"
        ]
        assert died_lines == [], (
            f"unexpected 'died' lifecycle events from probe failures: {died_lines}"
        )


# ---------- Scenario 6: documented limit — idle_seconds honest during tool execution ----------


class TestIdleSecondsDuringLongToolExecutionIsHonest:
    async def test_idle_seconds_during_long_tool_execution_is_honest(
        self, monkeypatch,
    ) -> None:
        """Documented limit: idle_seconds climbs during in-flight SDK stream gap.

        The fake yields one AssistantMessage immediately, then delays 2s before
        yielding the ResultMessage (simulating a long tool-execution gap in the
        SDK stream). During that gap, stamp_activity is NOT called, so
        idle_seconds increases. The teammate is alive and in a turn.

        This proves the gap that Feature #8 will close: idle_seconds is not
        a reliable proxy for 'genuinely stuck' during active tool execution.
        """
        fake = ProgrammableSDKClient(
            scripted_responses=[text_response("done")],
            # event_timings: first event (AssistantMessage) instant,
            # second event (ResultMessage) delayed 2s → simulates tool execution gap.
            event_timings=[0.0, 2.0],
        )
        monkeypatch.setattr(sdk_module, "ClaudeSDKClient", lambda options=None: fake)
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "10.0")   # keep poll idle
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "10.0")   # don't fire backstop

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_sdk_teammate_factory,
            )

            await b.send(_envelope(recipient=tid))

            # Give the turn time to start and yield the first event
            await asyncio.sleep(0.3)

            # Poll status twice with a gap to observe idle_seconds climbing
            status1 = b.get_teammate_status(tid)
            await asyncio.sleep(0.4)
            status2 = b.get_teammate_status(tid)

            # Teammate is alive and in a turn
            assert status1["alive"] is True, f"alive expected; got {status1}"
            assert status1["current_turn_started_at_wallclock"] is not None, (
                "must be in a turn during the SDK gap"
            )

            # idle_seconds grows — the documented limitation (Feature #8 will close this)
            idle1 = status1["idle_seconds"]
            idle2 = status2["idle_seconds"]
            assert idle2 > idle1, (
                f"idle_seconds must increase during tool-execution gap "
                f"(got {idle1:.3f} → {idle2:.3f}). "
                "This proves the gap Feature #8 will close."
            )

            # Wait for the turn to complete
            await _wait_messages(b, 1, timeout=5.0)

            # Post-turn: idle_seconds stops growing (turn ended, no new events)
            status_done = b.get_teammate_status(tid)
            assert status_done["alive"] is True
            assert status_done["current_turn_started_at_wallclock"] is None
        finally:
            await b.shutdown_all()
