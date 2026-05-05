"""Live SDK tests. Gated by CLAUDE_CREW_LIVE_TESTS=1.

These tests cost real money and require working Claude credentials.
SC-4 (UUID recall over 10+ turns) and the SC-5 spike (CLAUDE.md
loading via setting_sources) live here.

AT-13: Task tool granted via extra_tools — verifies whether the Task tool
is functional in an SDK subprocess context (or fails informatively).

AT-12: extra_tools merge reaches SDK subprocess (knowledge-graph probe).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory
from claude_crew.subagents._user_loader import _load_user_mcp_server_names


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


def _has_kg_server() -> bool:
    """True if the knowledge-graph MCP server is registered in ~/.claude.json."""
    return "knowledge-graph" in _load_user_mcp_server_names()


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 90.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}",
    )


async def _send_and_wait(broker: Broker, tid: str, prompt: str, expected_count: int) -> Envelope:
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload=prompt,
    ))
    await _wait_for_lead(broker, expected_count)
    msgs = broker.get_messages(recipient=LEAD_ID)
    return msgs[-1]


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


class TestUUIDRecallOver10Turns:
    """SC-4: a teammate must remember information across many turns
    within a single session. Deterministic — exact UUID substring match,
    no semantic-resemblance judgment."""

    async def test_uuid_recall(self, broker: Broker) -> None:
        token = str(uuid.uuid4())
        tid = await broker.spawn_teammate(
            role="recall-test", name=None, factory=sdk_factory,
        )

        # Turn 1: plant the token.
        await _send_and_wait(
            broker, tid,
            f"I'm going to give you a token to remember. The token is: {token} "
            f"Please acknowledge that you've stored it. I'll quiz you in 9 more turns.",
            expected_count=1,
        )

        # Turns 2-9: unrelated conversation.
        chatter = [
            "What is 17 times 23?",
            "Name three rivers in Africa.",
            "What is the boiling point of water in Celsius?",
            "Spell the word 'rhythm'.",
            "What year did the Apollo 11 mission land on the moon?",
            "What is the largest mammal?",
            "Translate 'good morning' to French.",
            "What is 2 to the 10th power?",
        ]
        for i, q in enumerate(chatter, start=2):
            await _send_and_wait(broker, tid, q, expected_count=i)

        # Turn 10: ask for the token verbatim.
        last = await _send_and_wait(
            broker, tid,
            "Now please repeat the exact token I asked you to remember on turn 1. "
            "Reply with just the token, nothing else.",
            expected_count=10,
        )

        assert last.payload.get("text"), f"final response had no text: {last.payload}"
        assert token in last.payload["text"], (
            f"UUID {token} not found in final response: {last.payload['text']!r}"
        )


class TestClaudeMdLoading:
    """SC-5 spike: CLAUDE.md is loaded only when setting_sources is set."""

    async def test_claude_md_loads_with_setting_sources(self, broker: Broker) -> None:
        from claude_crew.sdk_teammate import SdkTeammate

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                setting_sources=["user", "project"],
            )
        factory.requires_auth = True

        tid = await broker.spawn_teammate(role="r", name=None, factory=factory)
        result = await _send_and_wait(
            broker, tid,
            "Read my user-level CLAUDE.md (should already be in your context). "
            "Tell me ONE word: what name is the human in this workspace called? "
            "Just the name, no other words.",
            expected_count=1,
        )
        text = result.payload.get("text", "").lower()
        assert "jerome" in text or "kael" in text, (
            f"expected 'jerome' or 'kael' in response; got: {text!r}"
        )

    async def test_claude_md_loaded_even_with_setting_sources_none(
        self, broker: Broker,
    ) -> None:
        # Empirical finding: the Claude CLI's defaults include loading CLAUDE.md.
        # Passing setting_sources=None to the SDK does NOT suppress this — it
        # only declines to override the CLI's defaults. Test documents the
        # behavior so future regressions in the SDK that DO start gating
        # CLAUDE.md on setting_sources are caught here.
        from claude_crew.sdk_teammate import SdkTeammate

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                setting_sources=None,
            )
        factory.requires_auth = True

        tid = await broker.spawn_teammate(role="r", name=None, factory=factory)
        result = await _send_and_wait(
            broker, tid,
            "Read my user-level CLAUDE.md (should already be in your context). "
            "Tell me ONE word: what name is the human in this workspace called? "
            "Just the name, no other words.",
            expected_count=1,
        )
        text = result.payload.get("text", "").lower()
        assert "jerome" in text or "kael" in text, (
            f"expected CLAUDE.md to be loaded by CLI default even with "
            f"setting_sources=None; got: {text!r}"
        )


class TestA2ConcurrentInterruptDuringDrain:
    """A2 live probe: interrupt() is safe to call concurrently with receive_response drain."""

    async def test_interrupt_during_drain(self) -> None:
        """A2: client.interrupt() does not corrupt or deadlock an active receive_response drain.

        If A2 is wrong (interrupt() is NOT concurrency-safe with an in-flight drain),
        S2 (stale-response delivery) reappears silently at the 1-hour backstop boundary
        and is very hard to diagnose. This test makes that assumption explicit and
        detectable.

        The test issues a slow query, starts draining concurrently, and calls interrupt()
        after a brief delay. Both drain and interrupt must complete without raising.
        """
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

        options = ClaudeAgentOptions(model="claude-haiku-4-5")
        async with ClaudeSDKClient(options=options) as client:
            await client.query(
                "Count slowly from 1 to 100, one number per line. Take your time.",
                session_id="a2-probe",
            )

            async def drain() -> None:
                async for _ in client.receive_response():
                    pass

            async def interrupt_after_delay() -> None:
                await asyncio.sleep(0.5)
                await client.interrupt()

            # Both must complete without raising; 30s is generous
            await asyncio.wait_for(
                asyncio.gather(drain(), interrupt_after_delay()),
                timeout=30.0,
            )


class TestMemoryDocExists:
    """SC-5: the empirical doc must exist with the three required sections."""

    def test_memory_doc_present(self) -> None:
        from pathlib import Path
        doc = Path(__file__).resolve().parent.parent / "doc" / "research" / "sdk-memory.md"
        assert doc.exists(), f"missing: {doc}"
        content = doc.read_text()
        assert "## 1. Conversation persistence" in content
        assert "## 2. CLAUDE.md loading" in content
        assert "## 3. Auto-memory subsystem" in content


class TestPermissionModeAndCwdLive:
    """SC-9b: plan mode prevents file write; control with cwd creates file.

    Behavioral proof of Feature #10 Task 4: that permissionMode and cwd
    are correctly wired from pack file → AgentDefinition → SdkTeammate
    → ClaudeAgentOptions → SDK behavior.
    """

    async def test_plan_mode_blocks_file_write_and_cwd_works(
        self, broker: Broker, tmp_path,
    ) -> None:
        """plan mode blocks Write; control creates file, proving cwd wiring works."""
        from pathlib import Path
        from claude_agent_sdk.types import AgentDefinition

        # Create separate directories for plan and control teammates.
        plan_dir = tmp_path / "plan_agent"
        plan_dir.mkdir()
        ctrl_dir = tmp_path / "ctrl_agent"
        ctrl_dir.mkdir()

        # Define two agents: plan-mode (no file creation) vs control (can write).
        plan_agent = AgentDefinition(
            description="Plan-mode agent that cannot execute tools",
            prompt="You are a planning agent. You cannot execute any tools.",
            model="claude-haiku-4-5-20251001",
            tools=["Read", "Write"],
            permissionMode="plan",
        )
        control_agent = AgentDefinition(
            description="Control agent with normal permissions",
            prompt="You are a general agent. You can execute tools normally.",
            model="claude-haiku-4-5-20251001",
            tools=["Read", "Write"],
        )

        # Spawn plan teammate.
        plan_tid = await broker.spawn_teammate(
            role="plan-role",
            name="plan",
            factory=lambda id, name, role, **_kw: sdk_factory(
                id=id, name=name, role=role,
                agents={"plan-role": plan_agent},
                cwd=str(plan_dir),
                permission_mode="plan",
            ),
        )

        # Spawn control teammate.
        ctrl_tid = await broker.spawn_teammate(
            role="ctrl-role",
            name="ctrl",
            factory=lambda id, name, role, **_kw: sdk_factory(
                id=id, name=name, role=role,
                agents={"ctrl-role": control_agent},
                cwd=str(ctrl_dir),
            ),
        )

        # Task for plan teammate: try to write a file (should be blocked by plan mode).
        plan_task = (
            "Write the string 'probe' to a file named probe.txt in the current directory. "
            "Use the Write tool."
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=plan_tid, timestamp=0.0,
            payload=plan_task,
        ))

        # Task for control teammate: write a file (should succeed).
        ctrl_task = (
            "Write the string 'probe' to a file named probe.txt in the current directory. "
            "Use the Write tool."
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=ctrl_tid, timestamp=0.0,
            payload=ctrl_task,
        ))

        # Wait for both teammates to complete (2 messages expected).
        await _wait_for_lead(broker, 2, timeout=120.0)

        # Check results.
        # Plan mode: probe.txt should NOT exist (plan mode blocked Write).
        plan_probe_exists = (plan_dir / "probe.txt").exists()
        assert plan_probe_exists is False, (
            "plan mode should have blocked Write tool; probe.txt should not exist"
        )

        # Control: probe.txt should exist and contain the probe string.
        ctrl_probe_exists = (ctrl_dir / "probe.txt").exists()
        assert ctrl_probe_exists is True, (
            f"control teammate should have created probe.txt in {ctrl_dir}"
        )
        ctrl_probe_content = (ctrl_dir / "probe.txt").read_text()
        assert "probe" in ctrl_probe_content, (
            f"probe.txt should contain 'probe'; got: {ctrl_probe_content!r}"
        )


@pytest.mark.skipif(
    not _has_kg_server(),
    reason=(
        "knowledge-graph MCP server not registered in ~/.claude.json — "
        "skipping live extra_tools subprocess probe"
    ),
)
class TestExtraToolsReachSdkSubprocess:
    """AT-12: extra_tools merge reaches the SDK subprocess.

    Proves that granting mcp__knowledge-graph__repo_map via extra_tools
    makes the tool accessible inside the spawned teammate session — the
    merge must reach the SDK CLI arguments, not just the broker snapshot.

    Gated by: CLAUDE_CREW_LIVE_TESTS=1 AND knowledge-graph in ~/.claude.json.
    """

    async def test_extra_tools_reach_sdk_subprocess(self, broker: Broker, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        from claude_crew.factories import default_factory

        factory = default_factory()
        tid = await broker.spawn_teammate(
            role="rr-planner",
            name=None,
            factory=factory,
            extra_tools=["mcp__knowledge-graph__repo_map"],
        )

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload=(
                "Use the mcp__knowledge-graph__repo_map tool to give me a brief "
                "repo map of the current project. Call the tool and report the "
                "first 3 entries you receive back."
            ),
        ))
        await _wait_for_lead(broker, 1, timeout=120.0)
        msgs = broker.get_messages(recipient=LEAD_ID)
        result = msgs[-1]
        text = (
            result.payload.get("text", "") if isinstance(result.payload, dict)
            else str(result.payload)
        )

        assert text.strip(), f"empty response from rr-planner: {result.payload!r}"

        # The tool must be accessible — response must NOT contain tool-unavailable phrases.
        unavailable_phrases = [
            "tool not available",
            "don't have access to",
            "do not have access to",
            "no tool named",
            "cannot use that tool",
            "can't use that tool",
            "unable to use",
            "tool is not",
        ]
        lower_text = text.lower()
        for phrase in unavailable_phrases:
            assert phrase not in lower_text, (
                f"extra_tools merge appears to have NOT reached the SDK subprocess; "
                f"tool-unavailable phrase {phrase!r} found in response: {text!r}"
            )


class TestTaskToolInSdkSubprocess:
    """AT-13: Task tool granted via extra_tools — functional or informative failure.

    The Task tool is Claude Code's built-in subagent primitive. SDK-spawned
    teammates may or may not have it available. This test grants it and asks
    the teammate to actually use it; we observe whether it works, is silently
    absent, or produces a clear error. The result determines whether the guard
    should be restored or permanently removed.
    """

    async def test_task_tool_functional_in_sdk_subprocess(self, broker: Broker, monkeypatch) -> None:
        monkeypatch.setenv("CLAUDE_CREW_TEAMMATE_MODE", "sdk")
        from claude_crew.factories import default_factory

        factory = default_factory()
        tid = await broker.spawn_teammate(
            role="rr-planner",
            name=None,
            factory=factory,
            extra_tools=["Task"],
        )

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload=(
                "Use the Task tool to spawn a subagent with this exact prompt: "
                "'Reply with only the word CONFIRMED and nothing else.' "
                "Then report back what the subagent replied. "
                "If you do not have the Task tool available, say exactly: "
                "TASK_TOOL_UNAVAILABLE"
            ),
        ))
        await _wait_for_lead(broker, 1, timeout=120.0)
        msgs = broker.get_messages(recipient=LEAD_ID)
        result = msgs[-1]
        text = (
            result.payload.get("text", "") if isinstance(result.payload, dict)
            else str(result.payload)
        )

        assert text.strip(), f"empty response from teammate: {result.payload!r}"

        if "TASK_TOOL_UNAVAILABLE" in text:
            pytest.fail(
                "Task tool is NOT available in SDK subprocess context — "
                "restore the guard in server.py or document the limitation. "
                f"Full response: {text!r}"
            )
        elif "CONFIRMED" in text:
            # Task tool worked — guard can stay removed.
            pass
        else:
            # Ambiguous — log the full response for manual inspection.
            pytest.fail(
                f"Unexpected response — could not determine Task tool status. "
                f"Response: {text!r}"
            )


class TestMemoryPersistence:
    """SC-5: memory written in session N appears injected in session N+1.

    Also verifies the server does NOT mutate MEMORY.md at spawn time.
    """

    ROLE = "live-memory-probe"
    MARKER = f"live-memory-marker-{uuid.uuid4().hex[:8]}"

    def _memory_path(self) -> "Path":
        from pathlib import Path
        from claude_crew.teammate_memory import memory_file_path
        return memory_file_path(self.ROLE)

    def _make_factory(self, *, tools: list[str]) -> "Any":
        from claude_agent_sdk.types import AgentDefinition
        agent_def = AgentDefinition(
            description="Memory probe agent",
            prompt="You are a memory probe. Follow instructions precisely.",
            model="claude-haiku-4-5-20251001",
            tools=tools,
            memory="user",
        )

        def factory(id, name, role, **_kw):
            from claude_crew.factories import sdk_factory
            return sdk_factory(
                id=id, name=name, role=role,
                agents={self.ROLE: agent_def},
            )

        return factory

    async def test_memory_persists_across_sessions(self, broker: Broker) -> None:
        from pathlib import Path
        from claude_crew.teammate_memory import memory_file_path, memory_index_path
        from claude_crew.teammate_prompt import SENTINEL_MEMORY

        path = memory_file_path(self.ROLE)
        index_path = memory_index_path()

        # Clean up any prior run.
        if path.exists():
            path.unlink()

        index_before = index_path.read_text() if index_path.exists() else None

        # --- Session N: write a marker to memory ---
        factory_w = self._make_factory(tools=["Write"])
        tid = await broker.spawn_teammate(role=self.ROLE, name=None, factory=factory_w)

        write_prompt = (
            f"Write the following content to this exact file path using the Write tool:\n\n"
            f"Path: {path}\n\n"
            f"Content:\n---\nname: {self.ROLE} memory\ndescription: live probe memory\ntype: user\n---\n\n"
            f"Marker: {self.MARKER}\n\n"
            f"After writing, confirm with 'WRITE_DONE'."
        )
        response = await _send_and_wait(broker, tid, write_prompt, expected_count=1)
        text = response.payload.get("text", "") if isinstance(response.payload, dict) else str(response.payload)
        assert "WRITE_DONE" in text or path.exists(), (
            f"Session N: write not confirmed and file not found. Response: {text!r}"
        )
        assert path.exists(), f"Memory file not created: {path}"
        assert self.MARKER in path.read_text(), "Marker not in written file"

        # Verify server did NOT mutate MEMORY.md.
        index_after = index_path.read_text() if index_path.exists() else None
        assert index_before == index_after, (
            "Server mutated MEMORY.md at spawn time — violates MEMORY.md mutation policy"
        )

        await broker.shutdown_all()

        # --- Session N+1: spawn fresh broker, same role, verify injection ---
        broker2 = Broker()
        try:
            factory_r = self._make_factory(tools=["Read", "Write"])
            tid2 = await broker2.spawn_teammate(role=self.ROLE, name=None, factory=factory_r)

            # Ask the teammate what it remembers — the marker must appear via injection.
            recall_response = await _send_and_wait(
                broker2, tid2,
                "What marker string do you have in your memory from prior sessions? "
                "Reply with just the marker.",
                expected_count=1,
            )
            recall_text = (
                recall_response.payload.get("text", "")
                if isinstance(recall_response.payload, dict)
                else str(recall_response.payload)
            )
            assert self.MARKER in recall_text, (
                f"SC-5 FAIL: marker {self.MARKER!r} not found in session N+1 response. "
                f"Response: {recall_text!r}"
            )
        finally:
            await broker2.shutdown_all()
            # Clean up memory file.
            if path.exists():
                path.unlink()
