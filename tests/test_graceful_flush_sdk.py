"""AT#1, AT#2, AT#3 — SdkTeammate graceful flush (non-live branch tests).

Uses a fake SDK client stand-in so no real Claude CLI subprocess is needed.

AT#1 — idle path: sentinel breaks inbox wait; flush query fires; event set.
AT#2 — busy path: interrupt breaks in-flight drain; flush query fires; event set.
AT#3 — surface detection: has_memory_surface() varies by scope and Write presence.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, AsyncIterator

import pytest
from claude_agent_sdk.types import AgentDefinition, AssistantMessage, ResultMessage, TextBlock

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import Broker
from claude_crew.sdk_teammate import (
    FLUSH_PROMPT,
    GRACEFUL_FLUSH_SECONDS,
    SdkTeammate,
)


# ---------------------------------------------------------------------------
# Fake client helpers
# ---------------------------------------------------------------------------


def _text_result(text: str) -> list[Any]:
    """Minimal scripted response: AssistantMessage + ResultMessage."""
    return [
        AssistantMessage(content=[TextBlock(text=text)], model="fake"),
        ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="fake",
        ),
    ]


class _SimpleFakeClient:
    """Minimal fake SDK client for idle-path flush tests.

    Records every query() call.  receive_response() returns the scripted
    responses in order.  interrupt() is a no-op (never called in idle path).
    """

    def __init__(self, responses: list[list[Any]]) -> None:
        self._responses = responses
        self._call_index = 0
        self.queries_received: list[tuple[str, str]] = []

    async def __aenter__(self) -> "_SimpleFakeClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self.queries_received.append((prompt, session_id))
        # Advance the scripted index so receive_response knows which turn we're on.
        self._call_index = len(self.queries_received)

    async def receive_response(self) -> AsyncIterator[Any]:  # type: ignore[return]
        idx = self._call_index - 1
        events = (
            self._responses[idx]
            if idx < len(self._responses)
            else _text_result(f"<default response #{idx + 1}>")
        )
        for msg in events:
            yield msg

    async def interrupt(self) -> None:
        pass  # unused in idle path


class _BlockingInterruptibleClient:
    """Fake SDK client for busy-path flush tests.

    First receive_response() hangs until interrupt() signals the internal
    event.  Second receive_response() yields the scripted flush response.
    Records interrupt() calls so tests can assert exactly one was made.
    """

    def __init__(self, flush_responses: list[Any]) -> None:
        self._interrupt_event: asyncio.Event = asyncio.Event()
        self._flush_responses = flush_responses
        self._call_count = 0
        self.queries_received: list[tuple[str, str]] = []
        self.interrupt_calls: list[float] = []

    async def __aenter__(self) -> "_BlockingInterruptibleClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def query(self, prompt: str, session_id: str = "default") -> None:
        self._call_count += 1
        self.queries_received.append((prompt, session_id))

    async def receive_response(self) -> AsyncIterator[Any]:  # type: ignore[return]
        if self._call_count == 1:
            # First (in-flight) turn: block until interrupt() sets the event.
            await self._interrupt_event.wait()
            # Return without yielding — empty iterator (interrupted drain).
            return
        # Flush turn: yield the scripted response.
        for msg in self._flush_responses:
            yield msg

    async def interrupt(self) -> None:
        self.interrupt_calls.append(time.monotonic())
        self._interrupt_event.set()


# ---------------------------------------------------------------------------
# Common setup helpers
# ---------------------------------------------------------------------------


def _make_memory_agent_def(
    memory: str | None = "project",
    tools: list[str] | None = None,
) -> AgentDefinition:
    """Return an AgentDefinition with the given memory scope and tools."""
    return AgentDefinition(
        description="flush-test agent",
        prompt="flush-test prompt",
        tools=tools if tools is not None else ["Write"],
        memory=memory,  # type: ignore[arg-type]
    )


def _make_sdk_teammate(
    role: str = "flusher",
    memory: str | None = "project",
    tools: list[str] | None = None,
) -> SdkTeammate:
    """Create a minimal SdkTeammate with the given memory scope."""
    agent_def = _make_memory_agent_def(memory=memory, tools=tools)
    return SdkTeammate(
        id="flush-tm",
        name="flush-tester",
        role=role,
        agents={role: agent_def},
        pack_bodies={role: "## Role\nFlush tester.\n"},
    )


def _patch_client(monkeypatch: Any, fake: Any) -> None:
    """Monkey-patch sdk_module.ClaudeSDKClient with the given fake."""
    def _ctor(options: Any = None) -> Any:
        fake.options = options
        return fake

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _ctor)


# ---------------------------------------------------------------------------
# AT#3 — surface detection (pure unit tests, no async, no broker)
# ---------------------------------------------------------------------------


class TestHasMemorySurface:
    """AT#3: has_memory_surface() reflects scope + Write presence."""

    def test_project_scope_with_write_is_true(self) -> None:
        """memory='project' → ensure_write_tool adds Write → True."""
        tm = _make_sdk_teammate(memory="project", tools=["Write"])
        assert tm.has_memory_surface() is True

    def test_user_scope_is_true(self) -> None:
        tm = _make_sdk_teammate(memory="user", tools=["Write"])
        assert tm.has_memory_surface() is True

    def test_local_scope_is_true(self) -> None:
        tm = _make_sdk_teammate(memory="local", tools=["Write"])
        assert tm.has_memory_surface() is True

    def test_no_memory_scope_is_false(self) -> None:
        """memory=None → False regardless of tools."""
        tm = _make_sdk_teammate(memory=None, tools=["Write", "Read"])
        assert tm.has_memory_surface() is False

    def test_scope_but_write_absent_is_false(self) -> None:
        """Scope present but Write NOT in effective tools → False.

        In normal __init__ flow, ensure_write_tool would add Write for any
        memory-scoped role.  This test directly mutates _agents to simulate
        a hypothetical edge case where Write is absent, verifying the
        belt-and-suspenders Write check.
        """
        tm = _make_sdk_teammate(memory="project")
        # Verify it starts True (Write was added by ensure_write_tool).
        assert tm.has_memory_surface() is True
        # Directly remove Write from the role's effective tools.
        original_def = tm._agents[tm.role]
        patched_def = dataclasses.replace(original_def, tools=["Read"])
        tm._agents = {**tm._agents, tm.role: patched_def}
        assert tm.has_memory_surface() is False

    def test_scope_with_none_tools_is_false(self) -> None:
        """Scope present but tools=None on the agent def → False."""
        tm = _make_sdk_teammate(memory="project")
        # Force tools=None via direct mutation (belt-and-suspenders check).
        original_def = tm._agents[tm.role]
        patched_def = dataclasses.replace(original_def, tools=None)
        tm._agents = {**tm._agents, tm.role: patched_def}
        assert tm.has_memory_surface() is False


# ---------------------------------------------------------------------------
# AT#1 — idle-path flush (SdkTeammate blocked on inbox.get())
# ---------------------------------------------------------------------------


class TestIdleFlushHappy:
    """AT#1: sentinel breaks inbox wait; fake client receives flush query; event set."""

    @pytest.mark.asyncio
    async def test_idle_flush_fires_flush_prompt(self, monkeypatch: Any) -> None:
        """Flush query carries FLUSH_PROMPT; _flush_complete is set on return."""
        fake = _SimpleFakeClient(responses=[_text_result("Memory saved.")])
        _patch_client(monkeypatch, fake)

        tm = _make_sdk_teammate(memory="project")
        broker = Broker()
        inbox: asyncio.Queue = asyncio.Queue()

        await tm.start(broker, inbox)
        try:
            # Wait for the liveness poll to signal readiness, then give the
            # event loop a tick so _run enters await inbox.get().
            await asyncio.wait_for(tm._poll_started.wait(), timeout=5.0)
            await asyncio.sleep(0)

            t0 = asyncio.get_event_loop().time()
            await asyncio.wait_for(
                tm.begin_graceful_termination(timeout=5.0),
                timeout=10.0,
            )
            elapsed = asyncio.get_event_loop().time() - t0

            assert elapsed < 5.0, f"flush took {elapsed:.2f}s, should be < 5s"
            assert tm._flush_complete.is_set(), "_flush_complete must be set after flush"
            assert len(fake.queries_received) == 1, (
                f"expected exactly 1 query; got {len(fake.queries_received)}"
            )
            assert fake.queries_received[0][0] == FLUSH_PROMPT, (
                f"expected FLUSH_PROMPT; got {fake.queries_received[0][0]!r}"
            )
        finally:
            await tm.shutdown()

    @pytest.mark.asyncio
    async def test_idle_flush_idempotent(self, monkeypatch: Any) -> None:
        """Calling begin_graceful_termination twice does not fire a second query."""
        fake = _SimpleFakeClient(responses=[_text_result("saved")])
        _patch_client(monkeypatch, fake)

        tm = _make_sdk_teammate(memory="project")
        broker = Broker()
        inbox: asyncio.Queue = asyncio.Queue()

        await tm.start(broker, inbox)
        try:
            await asyncio.wait_for(tm._poll_started.wait(), timeout=5.0)
            await asyncio.sleep(0)

            await tm.begin_graceful_termination(timeout=5.0)
            query_count_after_first = len(fake.queries_received)

            # Second call — _flush_complete is already set; should be instant.
            await asyncio.wait_for(
                tm.begin_graceful_termination(timeout=5.0), timeout=2.0
            )

            assert len(fake.queries_received) == query_count_after_first, (
                "second begin_graceful_termination must not fire additional queries"
            )
        finally:
            await tm.shutdown()


# ---------------------------------------------------------------------------
# AT#2 — busy-path flush (mid-_handle_one_turn when begin_graceful_termination called)
# ---------------------------------------------------------------------------


class TestBusyFlushHappy:
    """AT#2: interrupt breaks in-flight drain; flush query fires; event set."""

    @pytest.mark.asyncio
    async def test_busy_flush_fires_interrupt_then_flush_query(
        self, monkeypatch: Any,
    ) -> None:
        """interrupt() is called once; flush query carries FLUSH_PROMPT; event set."""
        from claude_crew.envelope import Envelope, new_message_id

        flush_resp = _text_result("Memory written.")
        fake = _BlockingInterruptibleClient(flush_responses=flush_resp)
        _patch_client(monkeypatch, fake)

        tm = _make_sdk_teammate(memory="project")
        broker = Broker()
        inbox: asyncio.Queue = asyncio.Queue()

        await tm.start(broker, inbox)
        try:
            await asyncio.wait_for(tm._poll_started.wait(), timeout=5.0)
            await asyncio.sleep(0)

            # Send a real envelope so the teammate enters _handle_one_turn and
            # blocks on the hanging receive_response().
            env = Envelope(
                id=new_message_id(),
                seq=0,
                sender="lead",
                recipient=tm.id,
                timestamp=time.time(),
                payload="do something slow",
            )
            await inbox.put(env)

            # Give the _run task time to:
            #   1. pick up the envelope
            #   2. call client.query(prompt, ...)
            #   3. enter asyncio.wait_for(_collect_response_text(...))
            #   4. await _interrupt_event.wait() inside receive_response
            await asyncio.sleep(0.1)

            # At this point the teammate is busy (current_turn_started_at_wallclock set).
            assert tm._current_turn_started_at_wallclock is not None, (
                "teammate should be busy (in a turn) before calling begin_graceful_termination"
            )

            t0 = asyncio.get_event_loop().time()
            await asyncio.wait_for(
                tm.begin_graceful_termination(timeout=5.0),
                timeout=10.0,
            )
            elapsed = asyncio.get_event_loop().time() - t0

            assert elapsed < 5.0, f"flush took {elapsed:.2f}s, should be < 5s"
            assert len(fake.interrupt_calls) == 1, (
                f"expected exactly 1 interrupt call; got {len(fake.interrupt_calls)}"
            )
            assert tm._flush_complete.is_set(), "_flush_complete must be set after flush"

            # Two queries total: the original in-flight turn + the flush turn.
            # The flush query must be the last one and carry FLUSH_PROMPT.
            assert len(fake.queries_received) >= 2, (
                f"expected at least 2 queries (in-flight + flush); "
                f"got {len(fake.queries_received)}"
            )
            last_prompt = fake.queries_received[-1][0]
            assert last_prompt == FLUSH_PROMPT, (
                f"last query must be FLUSH_PROMPT; got {last_prompt!r}"
            )
        finally:
            await tm.shutdown()

    @pytest.mark.asyncio
    async def test_busy_flush_sets_flush_complete_even_on_empty_drain(
        self, monkeypatch: Any,
    ) -> None:
        """_flush_complete is set even if the flush drain returns empty text."""
        from claude_crew.envelope import Envelope, new_message_id

        # Flush turn returns no text (ResultMessage only — empty assistant response).
        empty_flush_resp: list[Any] = [
            ResultMessage(
                subtype="success",
                duration_ms=0,
                duration_api_ms=0,
                is_error=False,
                num_turns=1,
                session_id="fake",
            )
        ]
        fake = _BlockingInterruptibleClient(flush_responses=empty_flush_resp)
        _patch_client(monkeypatch, fake)

        tm = _make_sdk_teammate(memory="project")
        broker = Broker()
        inbox: asyncio.Queue = asyncio.Queue()

        await tm.start(broker, inbox)
        try:
            await asyncio.wait_for(tm._poll_started.wait(), timeout=5.0)
            await asyncio.sleep(0)

            env = Envelope(
                id=new_message_id(),
                seq=0,
                sender="lead",
                recipient=tm.id,
                timestamp=time.time(),
                payload="work",
            )
            await inbox.put(env)
            await asyncio.sleep(0.1)

            await asyncio.wait_for(
                tm.begin_graceful_termination(timeout=5.0),
                timeout=10.0,
            )

            assert tm._flush_complete.is_set()
        finally:
            await tm.shutdown()
