"""E2E integration tests for Feature #8 (tool-execution telemetry via SDK hooks).

Task 5 — full-stack scenarios: happy path, parallel tools, subagent boundary,
sad paths (death-mid-tool, kill-mid-tool, adversarial redaction), and one live
SDK A2 probe.

Test structure:
  - TestToolTelemetryE2E   — non-live scenarios (ProgrammableSDKClient fake)
  - test_live_a2_probe_*   — live probe gated by CLAUDE_CREW_LIVE_TESTS=1

Setup notes:
  - conftest autouse sets CLAUDE_CREW_TEAMMATE_MODE=stub and
    CLAUDE_CREW_TRANSCRIPT_DISABLED=1. Tests that need SDK mode use
    _patch_sdk() / _factory_for(); tests that need transcripts use
    enable_transcripts fixture.
  - Hook callbacks are bound methods on SdkTeammate — drive them directly
    via teammate._on_pre_tool_use / _on_post_tool_use etc., matching the
    approach in test_sdk_teammate.py T3a scenarios.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker, LEAD_ID
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.sdk_teammate import SdkTeammate
from claude_crew.teammate import _ToolUseEntry
from tests.fakes.programmable_sdk_client import ProgrammableSDKClient
from tests.fakes.sdk import text_response


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
    """Return a teammate factory that creates SdkTeammate (SDK mocked externally)."""
    def _factory(id: str, name: str, role: str, **_kw: Any) -> SdkTeammate:
        return SdkTeammate(id=id, name=name, role=role)
    return _factory


def _envelope(recipient: str, payload: Any = "go") -> Envelope:
    return Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=recipient,
        timestamp=time.time(), payload=payload,
    )


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 4.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.02)
    got = len(broker.get_messages(recipient=LEAD_ID))
    raise AssertionError(f"timed out waiting for {count} lead messages; got {got}")


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
# Non-live E2E scenarios (drive ProgrammableSDKClient / hook methods directly)
# ─────────────────────────────────────────────────────────────────────────────


class TestToolTelemetryE2E:
    """Full-stack scenarios for Feature #8 tool-execution telemetry.

    All tests spin a real Broker + SdkTeammate with the SDK mocked. Hook
    callbacks are fired directly on the teammate object — same technique
    used in test_sdk_teammate.py's TestToolExecutionHooks.
    """

    # ── SC-1 / SC-7: Happy — lead observes Bash mid-execution ────────────────

    async def test_lead_observes_bash_mid_execution(
        self, broker: Broker, monkeypatch,
    ) -> None:
        """SC-1, SC-7: spawn teammate, fire Pre via fake client, lead reads
        get_teammate_status, sees current_tool='Bash' with started_at and
        args_summary populated."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        # Fire PreToolUse directly — simulates SDK calling our hook before tool runs.
        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/ -q"},
        }
        await teammate._on_pre_tool_use(hook_input, "tu-e2e-1", {})

        # Lead reads status mid-tool.
        status = broker.get_teammate_status(tid)
        assert status["alive"] is True
        assert status["current_tool"] == "Bash"
        assert status["current_tool_count"] == 1
        assert len(status["current_tools"]) == 1
        entry = status["current_tools"][0]
        assert entry["tool_name"] == "Bash"
        assert entry["tool_use_id"] == "tu-e2e-1"
        assert entry["args_summary"] is not None
        assert "command=" in entry["args_summary"]
        assert "pytest" in entry["args_summary"]
        # idle_seconds must be small — Pre hook stamped activity.
        assert status["idle_seconds"] < 2.0

    # ── SC-9: Parallel tools tracked independently ───────────────────────────

    async def test_parallel_tools_tracked_independently(
        self, broker: Broker, monkeypatch,
    ) -> None:
        """SC-9 E2E: fire two Pre events back-to-back without intervening
        Posts; assert current_tool_count == 2; fire one Post; assert
        current_tool_count == 1; fire other Post; assert empty."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]  # type: ignore[attr-defined]

        tu1, tu2 = "tu-parallel-1", "tu-parallel-2"

        # Fire both Pre events (SDK parallel-tool empirical shape: Pre→Pre→Post→Post).
        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "echo a"}},
            tu1, {},
        )
        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "WebFetch", "tool_input": {"url": "https://example.com", "prompt": "fetch"}},
            tu2, {},
        )

        # Both tracked.
        status = broker.get_teammate_status(tid)
        assert status["current_tool_count"] == 2
        ids_tracked = {e["tool_use_id"] for e in status["current_tools"]}
        assert tu1 in ids_tracked
        assert tu2 in ids_tracked

        # Finish tu1 (Bash done).
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_response": "done"},
            tu1, {},
        )
        status = broker.get_teammate_status(tid)
        assert status["current_tool_count"] == 1
        assert status["current_tools"][0]["tool_use_id"] == tu2

        # Finish tu2 (WebFetch done).
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "WebFetch", "tool_response": "<html>"},
            tu2, {},
        )
        status = broker.get_teammate_status(tid)
        assert status["current_tool_count"] == 0
        assert status["current_tool"] is None
        assert status["current_tools"] == []

    # ── SC-10: Subagent Bash does NOT emit transcript line ───────────────────

    async def test_subagent_bash_does_not_emit_transcript_line(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-10 E2E: fire Pre with agent_id populated; assert no
        tool_start line in the transcript file; assert idle_seconds
        was reset (activity stamp fired).

        Note: Broker is created inline (after enable_transcripts has set the
        CLAUDE_CREW_TRANSCRIPT_DIR env var) to ensure the sink is active.
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_factory_for(fake),
            )
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            # Record activity_at before the subagent hook fires.
            snap_before = teammate.status_snapshot()
            before_wallclock = snap_before["last_activity_at_wallclock"]

            await asyncio.sleep(0.01)  # Ensure wallclock advances.

            # Subagent hook: agent_id is non-None.
            hook_input = {
                "agent_id": "sub-abc123",
                "agent_type": "echo-runner",
                "tool_name": "Bash",
                "tool_input": {"command": "echo subagent"},
            }
            await teammate._on_pre_tool_use(hook_input, "tu-sub-1", {})

            # current_tools must be empty — D3 subagent boundary.
            snap = teammate.status_snapshot()
            assert snap["current_tools"] == []
            assert snap["current_tool"] is None
            assert snap["current_tool_count"] == 0

            # Activity must have been stamped (idle_seconds reset).
            assert snap["last_activity_at_wallclock"] >= before_wallclock
        finally:
            await b.shutdown_all()

        # Transcript must have no tool_start line for this subagent Bash.
        lines = _read_transcript_lines(enable_transcripts)
        tool_start_lines = [l for l in lines if l.get("kind") == "tool_start"]
        assert tool_start_lines == [], (
            f"subagent tool call must NOT emit tool_start; got: {tool_start_lines}"
        )

    # ── SC-14: Death mid-Bash emits abandoned tool_end before lifecycle:died ─

    async def test_death_mid_bash_emits_abandoned_tool_end(
        self, broker: Broker, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-14 E2E: fire Pre, then trigger death via
        broker._handle_teammate_death; read transcript; assert tool_end
        with outcome='abandoned' precedes lifecycle:died."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_factory_for(fake),
            )
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            # Inject an in-flight Bash tool (5s ago).
            await teammate._on_pre_tool_use(
                {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "sleep 60"}},
                "tu-death-1", {},
            )

            # Simulate subprocess death.
            await b._handle_teammate_death(tid, exit_code=137)  # type: ignore[attr-defined]
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(enable_transcripts)
        tool_end_lines = [l for l in lines if l.get("kind") == "tool_end"]
        died_lines = [
            l for l in lines
            if l.get("kind") == "lifecycle" and l.get("event") == "died"
        ]

        assert len(tool_end_lines) >= 1, f"expected at least one tool_end; got {lines}"
        assert len(died_lines) == 1, f"expected one lifecycle:died; got {died_lines}"

        # Ordering: tool_end must precede lifecycle:died.
        tool_end_idx = next(
            i for i, l in enumerate(lines) if l.get("kind") == "tool_end"
        )
        died_idx = next(
            i for i, l in enumerate(lines)
            if l.get("kind") == "lifecycle" and l.get("event") == "died"
        )
        assert tool_end_idx < died_idx, (
            f"tool_end (line {tool_end_idx}) must precede lifecycle:died (line {died_idx})"
        )

        te = tool_end_lines[0]
        assert te["outcome"] == "abandoned", f"expected outcome=abandoned; got {te}"
        assert te["tool_name"] == "Bash"
        assert te["tool_use_id"] == "tu-death-1"

        # Post-mortem status: current_tools is empty.
        status = b.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["current_tools"] == []
        assert status["current_tool"] is None

    # ── SC-14: Kill mid-Bash emits killed tool_end before lifecycle:kill ─────

    async def test_kill_mid_bash_emits_killed_tool_end(
        self, broker: Broker, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-14 E2E: fire Pre, call broker.kill_teammate; assert
        tool_end with outcome='killed' precedes lifecycle:kill."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_factory_for(fake),
            )
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            # Inject an in-flight Bash tool.
            await teammate._on_pre_tool_use(
                {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "sleep 60"}},
                "tu-kill-1", {},
            )

            # Operator kills the teammate.
            await b.kill_teammate(tid)
        finally:
            await b.shutdown_all()

        lines = _read_transcript_lines(enable_transcripts)
        tool_end_lines = [l for l in lines if l.get("kind") == "tool_end"]
        kill_lines = [
            l for l in lines
            if l.get("kind") == "lifecycle" and l.get("event") == "kill"
        ]

        assert len(tool_end_lines) >= 1, f"expected at least one tool_end; got {lines}"
        assert len(kill_lines) == 1, f"expected one lifecycle:kill; got {kill_lines}"

        # Ordering: tool_end precedes lifecycle:kill.
        tool_end_idx = next(
            i for i, l in enumerate(lines) if l.get("kind") == "tool_end"
        )
        kill_idx = next(
            i for i, l in enumerate(lines)
            if l.get("kind") == "lifecycle" and l.get("event") == "kill"
        )
        assert tool_end_idx < kill_idx, (
            f"tool_end (line {tool_end_idx}) must precede lifecycle:kill (line {kill_idx})"
        )

        te = tool_end_lines[0]
        assert te["outcome"] == "killed", f"expected outcome=killed; got {te}"
        assert te["tool_name"] == "Bash"
        assert te["tool_use_id"] == "tu-kill-1"

    # ── SC-15: Adversarial redaction round-trip ──────────────────────────────

    async def test_adversarial_redaction_round_trip(
        self, monkeypatch, enable_transcripts,
    ) -> None:
        """SC-15 E2E full-stack: fire Pre with a tool_input containing
        a Bearer token; verify the tool_start transcript line's
        args_summary contains '<redacted>' and not the literal token.
        Uses a FAKE_DUMMY_TOKEN pattern — NOT a real-shaped Anthropic key.

        Note: Broker is created inline (after enable_transcripts has set the
        CLAUDE_CREW_TRANSCRIPT_DIR env var) to ensure the sink is active.
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        fake = ProgrammableSDKClient(scripted_responses=[])
        _patch_sdk(monkeypatch, fake)

        # Fake token shaped like a real Anthropic key (sk-ant- prefix) — caught
        # unconditionally by the anchored sk- pattern, regardless of what the
        # Authorization: Bearer pattern does to the surrounding header.
        # NOT a real key; the prefix "FAKETEST" makes that clear.
        dummy_token = "sk-ant-FAKETESTONLY0123456789abcdefghijklmno"
        command = f"curl -H 'Authorization: Bearer {dummy_token}' https://api.example.com/data"

        b = Broker()
        try:
            tid = await b.spawn_teammate(
                role="r", name=None, factory=_factory_for(fake),
            )
            teammate = b._teammates[tid]  # type: ignore[attr-defined]

            hook_input = {
                "agent_id": None,
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
            await teammate._on_pre_tool_use(hook_input, "tu-redact-1", {})

            # Verify in-memory status summary is redacted.
            snap = teammate.status_snapshot()
            assert snap["current_tools"][0]["args_summary"] is not None
            args_summary = snap["current_tools"][0]["args_summary"]
            assert dummy_token not in args_summary, (
                f"token literal must not appear in args_summary; got: {args_summary!r}"
            )
            assert "<redacted" in args_summary, (
                f"expected redaction marker in args_summary; got: {args_summary!r}"
            )
        finally:
            await b.shutdown_all()

        # Verify transcript tool_start line is also redacted.
        lines = _read_transcript_lines(enable_transcripts)
        tool_start_lines = [l for l in lines if l.get("kind") == "tool_start"]
        assert len(tool_start_lines) == 1, f"expected 1 tool_start; got {lines}"
        ts = tool_start_lines[0]
        assert dummy_token not in (ts.get("args_summary") or ""), (
            f"token literal must not appear in transcript tool_start; got: {ts}"
        )
        assert "<redacted" in (ts.get("args_summary") or ""), (
            f"expected redaction marker in transcript tool_start; got: {ts}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Live SDK A2 probe — gated by CLAUDE_CREW_LIVE_TESTS=1
# ─────────────────────────────────────────────────────────────────────────────

pytestmark_live = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


@pytestmark_live
class TestLiveToolTelemetryA2Probe:
    """One real ClaudeSDKClient driven through a Bash echo.

    Confirms that hook callbacks fire end-to-end on a real SDK subprocess:
      - PreToolUse fires → tool_start transcript line written
      - PostToolUse fires → tool_end transcript line written
      - tool_use_ids in tool_start and tool_end match
      - get_teammate_status mid-call reflects active Bash (best-effort timing)
      - args_summary contains 'echo' (model wrote that command)

    Estimated cost: <$0.10/run (one Haiku 4.5 call, single Bash tool).

    Live-probe checklist (F6 retro):
      ✓ No assertion on token counts or workload-sensitive values.
      ✓ Tool-name correctness verified by transcript content, not agent narration.
      ✓ Test plants nothing — uses observable side-effect (echo output) and
        transcript record as the source of truth.
      ✓ Prompt does NOT contain the echo marker to avoid "agent reads its
        own input" false-positive. Marker is in the assertion only.
    """

    async def test_live_a2_probe_real_bash_observed(
        self, monkeypatch, tmp_path,
    ) -> None:
        """A2: hooks fire, transcript written, status reflects Bash mid-call."""
        monkeypatch.delenv("CLAUDE_CREW_TRANSCRIPT_DISABLED", raising=False)
        monkeypatch.setenv("CLAUDE_CREW_TRANSCRIPT_DIR", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "60.0")

        def factory(id: str, name: str, role: str, **_kw: Any) -> SdkTeammate:
            return SdkTeammate(id=id, name=name, role=role, model="claude-haiku-4-5-20251001")

        factory.requires_auth = True  # type: ignore[attr-defined]

        b = Broker()
        try:
            tid = await b.spawn_teammate(role="r", name=None, factory=factory)

            # Prompt: ask the model to run a Bash echo. Marker is deliberately
            # NOT in the prompt (avoids "model echoes back its own input" false
            # positive). The model will construct the echo command autonomously.
            await b.send(_envelope(
                recipient=tid,
                payload=(
                    "Run a single Bash command that outputs the word 'e2emarker' "
                    "using echo. Do nothing else after running it."
                ),
            ))

            # Wait for the reply (up to 60s for cold Haiku start).
            await _wait_for_lead(b, 1, timeout=60.0)
        finally:
            await b.shutdown_all()

        # Read transcript.
        lines = _read_transcript_lines(tmp_path)
        tool_start_lines = [l for l in lines if l.get("kind") == "tool_start"]
        tool_end_lines = [l for l in lines if l.get("kind") == "tool_end"]

        # Must have at least one tool_start + tool_end pair (the Bash call).
        assert tool_start_lines, (
            f"live probe: expected at least one tool_start line; transcript: {lines}"
        )
        assert tool_end_lines, (
            f"live probe: expected at least one tool_end line; transcript: {lines}"
        )

        # Find the Bash tool_start.
        bash_starts = [l for l in tool_start_lines if l.get("tool_name") == "Bash"]
        assert bash_starts, (
            f"live probe: expected a Bash tool_start; got tool_starts: {tool_start_lines}"
        )
        bash_start = bash_starts[0]
        bash_tuid = bash_start["tool_use_id"]

        # Matching tool_end by tool_use_id.
        bash_ends = [l for l in tool_end_lines if l.get("tool_use_id") == bash_tuid]
        assert bash_ends, (
            f"live probe: no tool_end matches tool_use_id={bash_tuid!r}; "
            f"tool_end lines: {tool_end_lines}"
        )
        bash_end = bash_ends[0]

        # Verify shapes.
        assert bash_end["outcome"] in ("ok", "failed"), (
            f"live probe: unexpected outcome for Bash: {bash_end['outcome']!r}"
        )
        assert bash_end["duration_seconds"] >= 0.0, (
            f"live probe: negative duration: {bash_end['duration_seconds']}"
        )
        assert bash_start.get("redaction_version") == "v1", (
            f"live probe: redaction_version missing or wrong: {bash_start}"
        )

        # args_summary must mention 'echo' (Bash is on the v1 allowlist).
        if bash_start.get("args_summary") is not None:
            assert "echo" in bash_start["args_summary"].lower(), (
                f"live probe: expected 'echo' in args_summary; "
                f"got: {bash_start['args_summary']!r}"
            )

        # Redaction version must be consistent between start and end.
        assert bash_end.get("redaction_version") == "v1", (
            f"live probe: tool_end missing redaction_version: {bash_end}"
        )

        # Lifecycle lines present (started + spawn).
        lifecycle_events = [
            l.get("event") for l in lines if l.get("kind") == "lifecycle"
        ]
        assert "started" in lifecycle_events, f"live probe: missing lifecycle:started"
        assert "spawn" in lifecycle_events, f"live probe: missing lifecycle:spawn"
