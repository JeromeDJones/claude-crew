"""Implementation-level tests for SdkTeammate.

The SDK's ClaudeSDKClient is monkey-patched with FakeSDKClient or
ProgrammableSDKClient. Tests exercise the SdkTeammate against a real
Broker — same approach as test_stub_teammate.py.
"""

from __future__ import annotations

import asyncio
import time
import types
from typing import Any

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    TaskNotificationMessage,
    TextBlock,
    ToolUseBlock,
)

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.sdk_teammate import (
    INTERRUPT_GRACE_SECONDS,
    SHUTDOWN_TIMEOUT_SECONDS,
    SdkTeammate,
    TurnDrainResult,
    _SubagentUseEntry,
    _ClosedSubagentEntry,
    _classify_error,
    _collect_response_text,
    _payload_to_prompt,
    RateLimitedError,
)
from tests.fakes.sdk import FakeSDKClient, text_response, text_response_with_usage
from tests.fakes.programmable_sdk_client import ProgrammableSDKClient


# ---------- helpers ----------


def _make_factory_with_fake(fake: FakeSDKClient):
    """Return (factory, captured_options) — patches ClaudeSDKClient to fake."""
    captured: dict[str, Any] = {}

    def _ctor(options: Any = None):
        captured["options"] = options
        fake.options = options
        return fake

    return _ctor, captured


def _patch_sdk(monkeypatch, fake: FakeSDKClient):
    ctor, captured = _make_factory_with_fake(fake)
    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", ctor)
    return captured


def _factory_for(fake: FakeSDKClient):
    def _factory(id, name, role, **_kwargs):
        return SdkTeammate(id=id, name=name, role=role)
    return _factory


async def _wait_for_lead_messages(broker: Broker, count: int, timeout: float = 2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}",
    )


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------- pure helpers ----------


class TestPayloadToPrompt:
    def test_string_payload_passes_through(self) -> None:
        assert _payload_to_prompt("hello") == "hello"

    def test_none_returns_empty(self) -> None:
        assert _payload_to_prompt(None) == ""

    def test_dict_with_prompt_key(self) -> None:
        assert _payload_to_prompt({"prompt": "hi"}) == "hi"

    def test_arbitrary_dict_is_json(self) -> None:
        result = _payload_to_prompt({"foo": "bar"})
        assert "foo" in result and "bar" in result


class TestClassifyError:
    def test_rate_limited(self) -> None:
        assert _classify_error(RateLimitedError("hit")) == "rate_limited"

    def test_classify_internal_default(self) -> None:
        assert _classify_error(ValueError("x")) == "internal"


# ---------- round-trip ----------


class TestRoundTrip:
    async def test_simple_round_trip(self, broker, monkeypatch) -> None:
        fake = FakeSDKClient(scripted_responses=[text_response("hi back")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="parrot", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hello",
        ))
        await _wait_for_lead_messages(broker, 1)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert len(msgs) == 1
        assert msgs[0].sender == tid
        assert msgs[0].payload == {"text": "hi back", "from": "parrot"}
        # SC-16: session_id now uses crew-teammate format.
        expected_session_id = f"{broker.crew_id}-{tid}"
        assert fake.queries_received[0] == ("hello", expected_session_id)

    async def test_multi_turn_in_order(self, broker, monkeypatch) -> None:
        fake = FakeSDKClient(scripted_responses=[
            text_response("first"),
            text_response("second"),
            text_response("third"),
        ])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        for i in range(3):
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0,
                payload=f"q{i}",
            ))
        await _wait_for_lead_messages(broker, 3)

        msgs = broker.get_messages(recipient=LEAD_ID)
        texts = [m.payload["text"] for m in msgs]
        assert texts == ["first", "second", "third"]
        assert [q[0] for q in fake.queries_received] == ["q0", "q1", "q2"]
        # SC-16: all session_ids should use the crew-teammate format.
        expected_session_id = f"{broker.crew_id}-{tid}"
        assert all(q[1] == expected_session_id for q in fake.queries_received)

    async def test_setting_sources_default_is_user_project(
        self, broker, monkeypatch,
    ) -> None:
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.setting_sources == ["user", "project"]
        assert opts.model == "claude-sonnet-4-6"

    async def test_model_and_effort_propagate_to_options(
        self, broker, monkeypatch,
    ) -> None:
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        # Use a factory that respects model/effort/cwd/permission_mode kwargs from the broker.
        def factory(id, name, role, *, model=None, effort=None, cwd=None, permission_mode=None):
            kwargs = {}
            if model is not None:
                kwargs["model"] = model
            if effort is not None:
                kwargs["effort"] = effort
            if cwd is not None:
                kwargs["cwd"] = cwd
            if permission_mode is not None:
                kwargs["permission_mode"] = permission_mode
            return SdkTeammate(id=id, name=name, role=role, **kwargs)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=factory,
            model="claude-opus-4-7", effort="medium",
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.model == "claude-opus-4-7"
        assert opts.effort == "medium"


# ---------- error paths ----------


class TestErrorPaths:
    async def test_query_exception_produces_error_envelope_loop_continues(
        self, broker, monkeypatch,
    ) -> None:
        fake = FakeSDKClient(
            scripted_responses=[
                text_response("unreachable"),  # turn 1 raises
                text_response("turn-2-ok"),
            ],
            query_raises=[RuntimeError("boom"), None],
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q1",
        ))
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q2",
        ))
        await _wait_for_lead_messages(broker, 2)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "internal"
        assert "boom" in msgs[0].payload.get("message", "")
        assert msgs[1].payload.get("text") == "turn-2-ok"

        # Worker task must still be alive — explicit liveness assertion,
        # not just inferred from the second envelope arriving.
        sdk_tasks = [
            t for t in asyncio.all_tasks() if t.get_name() == f"sdk-{tid}"
        ]
        assert sdk_tasks and not sdk_tasks[0].done()

    async def test_informational_rate_limit_event_is_ignored(
        self, broker, monkeypatch,
    ) -> None:
        # status='allowed' or 'allowed_warning' is telemetry, not a failure.
        # The stream still contains a real AssistantMessage; the teammate
        # should produce a normal text envelope, not an error envelope.
        info_event = RateLimitEvent(
            rate_limit_info=RateLimitInfo(
                status="allowed", rate_limit_type="five_hour",
            ),
            uuid="evt-info",
            session_id="default",
        )
        fake = FakeSDKClient(scripted_responses=[
            [info_event] + text_response("normal reply"),
        ])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("text") == "normal reply"
        assert "error" not in msgs[0].payload

    async def test_rate_limit_event_produces_rate_limited_envelope(
        self, broker, monkeypatch,
    ) -> None:
        # The fake's first turn yields a RateLimitEvent in the stream;
        # _collect_response_text raises RateLimitedError; _handle_one_turn
        # produces an envelope with code "rate_limited" and the loop survives.
        rate_event = RateLimitEvent(
            rate_limit_info=RateLimitInfo(
                status="rejected", rate_limit_type="five_hour",
            ),
            uuid="evt-1",
            session_id="default",
        )
        fake = FakeSDKClient(scripted_responses=[
            [rate_event],  # turn 1: rate-limited
            text_response("recovered"),  # turn 2: normal
        ])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q1",
        ))
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q2",
        ))
        await _wait_for_lead_messages(broker, 2)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "rate_limited"
        assert msgs[1].payload.get("text") == "recovered"

    async def test_empty_prompt_skips_sdk_call(self, broker, monkeypatch) -> None:
        fake = FakeSDKClient(scripted_responses=[text_response("never sent")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="",
        ))
        await _wait_for_lead_messages(broker, 1)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "invalid_response"
        assert "empty prompt" in msgs[0].payload.get("message", "")
        assert fake.queries_received == []  # SDK not called

    async def test_response_hangs_produces_backstop_timeout_error(
        self, broker, monkeypatch,
    ) -> None:
        """Backstop fires when response hangs; backstop_timeout error sent.

        Updated from the old TURN_TIMEOUT_SECONDS-based test. Now uses
        ProgrammableSDKClient (which has interrupt()) and env-var backstop
        override so the test completes in under a second.
        """
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "0.1")
        # Keep POST_INTERRUPT_DRAIN short so the drain attempt doesn't delay
        # the test — FakeSDKClient's _hang stays True after interrupt(), so
        # the drain would also hang for the full POST_INTERRUPT_DRAIN_SECONDS.
        monkeypatch.setattr(sdk_module, "POST_INTERRUPT_DRAIN_SECONDS", 0.05)

        fake = ProgrammableSDKClient(
            scripted_responses=[[]],
            response_hangs=[True],
            interrupt_behavior="normal",
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q1",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=3.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "backstop_timeout"
        assert "backstop fired" in msgs[0].payload.get("message", "")
        assert len(fake.interrupt_calls) == 1

    async def test_empty_text_response_yields_invalid_response(
        self, broker, monkeypatch,
    ) -> None:
        # Tool-use only (Assumption A2): no TextBlocks → empty text → error.
        fake = FakeSDKClient(scripted_responses=[[
            AssistantMessage(
                content=[ToolUseBlock(id="tu-1", name="some_tool", input={})],
                model="fake",
            ),
            ResultMessage(
                subtype="success", duration_ms=0, duration_api_ms=0,
                is_error=False, num_turns=1, session_id="default",
            ),
        ]])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "invalid_response"
        assert "no text content" in msgs[0].payload.get("message", "")


# ---------- shutdown ----------


class TestShutdown:
    async def test_shutdown_closes_sdk_client(self, broker, monkeypatch) -> None:
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        await broker.kill_teammate(tid)

        # Broker uses detached create_task(shutdown()) — give it time to run.
        for _ in range(50):
            if fake.aexit_count >= 1:
                break
            await asyncio.sleep(0.05)

        assert fake.aenter_count == 1
        assert fake.aexit_count == 1
        # No teammate task should be alive.
        for _ in range(50):
            sdk_tasks = [
                t for t in asyncio.all_tasks()
                if t.get_name().startswith("sdk-")
            ]
            if all(t.done() for t in sdk_tasks):
                break
            await asyncio.sleep(0.05)
        sdk_tasks = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith("sdk-")
        ]
        assert all(t.done() for t in sdk_tasks)

    async def test_shutdown_during_in_flight_turn_respects_timeout(
        self, broker, monkeypatch,
    ) -> None:
        # Set the SHUTDOWN timeout small so the test is fast. The hung turn
        # never returns; the worker task is hard-cancelled.
        monkeypatch.setattr(sdk_module, "SHUTDOWN_TIMEOUT_SECONDS", 0.2)

        fake = FakeSDKClient(
            scripted_responses=[[]],
            response_hangs=[True],
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        # Wait until the worker has actually called query() — that's the
        # synchronization point that proves the turn is in flight, not a
        # wall-clock sleep that can race on slow runners.
        for _ in range(100):
            if fake.queries_received:
                break
            await asyncio.sleep(0.01)
        assert fake.queries_received, "worker never reached query()"

        start = asyncio.get_event_loop().time()
        await broker.kill_teammate(tid)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.5, f"shutdown took {elapsed:.2f}s"

        # Broker uses detached create_task(shutdown()) — poll until tasks done.
        for _ in range(50):
            sdk_tasks = [
                t for t in asyncio.all_tasks()
                if t.get_name().startswith("sdk-")
            ]
            if all(t.done() for t in sdk_tasks):
                break
            await asyncio.sleep(0.05)
        sdk_tasks = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith("sdk-")
        ]
        assert all(t.done() for t in sdk_tasks)


# ---------- T4: telemetry-liveness BDD scenarios ----------


class TestLivenessTelemetry:
    """BDD scenarios from Phase 3 T4 Acceptance Criteria."""

    async def test_long_turn_completes_no_wall(
        self, broker, monkeypatch,
    ) -> None:
        """SC-1: A turn that takes a long time completes without a wall.

        TURN_TIMEOUT_SECONDS must not exist as a module attribute (D10).
        Backstop is 10s; events have tiny real delays totaling << 10s.
        """
        assert not hasattr(sdk_module, "TURN_TIMEOUT_SECONDS"), (
            "TURN_TIMEOUT_SECONDS was NOT deleted (D10 violated)"
        )

        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "10.0")
        fake = ProgrammableSDKClient(
            scripted_responses=[text_response("long turn result")],
            event_timings=[0.01, 0.01],  # real delays; backstop is 10s — no fire
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="heavy",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=5.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert len(msgs) == 1
        assert msgs[0].payload.get("text") == "long turn result"
        assert "error" not in msgs[0].payload

    async def test_activity_stamps_advance_per_event(
        self, broker, monkeypatch,
    ) -> None:
        """SC-3: Every yielded event (including non-AssistantMessage types) stamps activity.

        The stamp callback runs at loop top, BEFORE any continue branch (D1).
        Even RateLimitEvent and TaskNotificationMessage hit the stamp.
        """
        # 5 events: RateLimitEvent, AssistantMessage, TaskNotificationMessage,
        # AssistantMessage, ResultMessage — each must stamp before any continue.
        rl_event = RateLimitEvent(
            rate_limit_info=RateLimitInfo(status="allowed", rate_limit_type="five_hour"),
            uuid="re-1", session_id="default",
        )
        tn_event = TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="t1",
            status="completed",
            output_file="",
            summary="done",
            uuid="tn-1",
            session_id="default",
        )
        scripted = [[
            rl_event,
            AssistantMessage(content=[TextBlock(text="part1")], model="fake"),
            tn_event,
            AssistantMessage(content=[TextBlock(text="part2")], model="fake"),
            ResultMessage(
                subtype="success", duration_ms=0, duration_api_ms=0,
                is_error=False, num_turns=1, session_id="default",
            ),
        ]]
        fake = ProgrammableSDKClient(scripted_responses=scripted)
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )

        # Capture the teammate reference before the turn starts so we can
        # wrap _stamp_activity to count calls.
        teammate = broker._teammates[tid]
        stamp_count = [0]
        original_stamp = teammate._stamp_activity

        def counting_stamp() -> None:
            stamp_count[0] += 1
            original_stamp()

        teammate._stamp_activity = counting_stamp  # type: ignore[method-assign]

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="go",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=2.0)

        # 5 events from drain + 1 from _begin_turn = 6 minimum.
        # (All 5 drain events stamp because stamp is at loop top before continue.)
        assert stamp_count[0] >= 6, (
            f"expected >=6 stamps (5 drain + 1 begin_turn), got {stamp_count[0]}"
        )

    async def test_no_s2_bleed_across_turns(
        self, broker, monkeypatch,
    ) -> None:
        """SC-7: Turn N+1 receives turn N+1's response — no stale-response bleed."""
        fake = ProgrammableSDKClient(
            scripted_responses=[
                text_response("alpha"),
                text_response("beta"),
            ],
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )

        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="q1",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=2.0)

        await broker.send(Envelope(
            id=new_message_id(), seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="q2",
        ))
        await _wait_for_lead_messages(broker, 2, timeout=2.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("text") == "alpha"
        assert msgs[1].payload.get("text") == "beta"
        # Explicitly verify bleed didn't occur.
        assert msgs[1].payload.get("text") != "alpha"

    async def test_backstop_fires_interrupt_succeeds(
        self, broker, monkeypatch,
    ) -> None:
        """SC-11: Backstop fires, interrupt succeeds — backstop_timeout error sent."""
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "0.2")
        # Shorten drain so FakeSDKClient's sticky _hang=True doesn't stall test.
        monkeypatch.setattr(sdk_module, "POST_INTERRUPT_DRAIN_SECONDS", 0.05)

        fake = ProgrammableSDKClient(
            scripted_responses=[[]],
            response_hangs=[True],
            interrupt_behavior="normal",
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="go",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=5.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "backstop_timeout"
        # Message should indicate interrupt was sent (succeeded).
        assert "sent" in msgs[0].payload.get("message", "")
        assert len(fake.interrupt_calls) == 1

    async def test_backstop_interrupt_hangs_escalates_to_death_suspected(
        self, broker, monkeypatch,
    ) -> None:
        """D4 co-architect: hung interrupt → _death_suspected=True."""
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "0.2")
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")  # keep poll out of the way
        # Override INTERRUPT_GRACE_SECONDS to make the test fast.
        monkeypatch.setattr(sdk_module, "INTERRUPT_GRACE_SECONDS", 0.1)

        fake = ProgrammableSDKClient(
            scripted_responses=[[]],
            response_hangs=[True],
            interrupt_behavior="hang",
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="go",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=5.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "backstop_timeout"
        assert "death-suspected" in msgs[0].payload.get("message", "")
        assert teammate._death_suspected is True

    async def test_backstop_interrupt_raises_escalates(
        self, broker, monkeypatch,
    ) -> None:
        """D4 sentinel: raising interrupt → _death_suspected=True."""
        monkeypatch.setenv("CLAUDE_CREW_TURN_BACKSTOP_SECONDS", "0.2")
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")

        fake = ProgrammableSDKClient(
            scripted_responses=[[]],
            response_hangs=[True],
            interrupt_behavior="raise",
            interrupt_raises=RuntimeError,
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="go",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=5.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "backstop_timeout"
        assert "death-suspected" in msgs[0].payload.get("message", "")
        assert teammate._death_suspected is True

    async def test_subprocess_dies_idle_poll_tombstones_within_window(
        self, broker, monkeypatch,
    ) -> None:
        """SC-5: Subprocess exits between turns — poll task tombstones within window."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "0.2")

        fake = ProgrammableSDKClient(
            scripted_responses=[],  # no turns; teammate just idles
            transport_returncode=None,
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )

        # Give the poll task a moment to start and enter its first sleep.
        await asyncio.sleep(0.05)

        # Simulate subprocess exit mid-idle.
        fake._transport._process.returncode = 137

        # Poll task fires every 0.2s; allow 2 poll cycles + margin.
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            info = broker._info.get(tid)
            if info is not None and not info.alive:
                break
            await asyncio.sleep(0.05)

        info = broker._info.get(tid)
        assert info is not None, "teammate info not found after expected death"
        assert not info.alive, "teammate should be tombstoned after subprocess exit"
        assert info.exit_code == 137

    async def test_sdk_death_midturn_handoff_via_in_flight(
        self, broker, monkeypatch,
    ) -> None:
        """SC-5b clause 1: SDK death mid-turn sets _death_in_flight_envelope;
        worker does NOT send an error envelope itself.
        """
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "30.0")  # keep poll quiet

        # Create a ProcessError-named class so the name-matching logic triggers.
        class ProcessError(Exception):
            pass

        fake = ProgrammableSDKClient(
            scripted_responses=[[]],
            read_raises=ProcessError,
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        # Capture ref before the turn (after death, teammate is removed from _teammates).
        teammate = broker._teammates[tid]

        env_id = new_message_id()
        await broker.send(Envelope(
            id=env_id, seq=0, sender=LEAD_ID, recipient=tid,
            timestamp=0.0, payload="go",
        ))

        # Wait until death_suspected is set by the worker's exception handler.
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline:
            if teammate._death_suspected:
                break
            await asyncio.sleep(0.02)

        assert teammate._death_suspected is True, (
            "worker should have set _death_suspected on ProcessError"
        )
        assert teammate._death_in_flight_envelope is not None, (
            "worker should have stored the in-flight envelope for the death handler"
        )
        # The worker must NOT have sent an error envelope directly.
        # (The broker's death handler may later bounce it, but the worker is silent.)
        lead_msgs = broker.get_messages(recipient=LEAD_ID)
        worker_error_msgs = [
            m for m in lead_msgs
            if m.sender == tid and m.payload.get("error") not in (None,)
        ]
        assert not worker_error_msgs, (
            f"worker should not send error envelope on ProcessError, got: {worker_error_msgs}"
        )

    async def test_probe_error_degrades_open(
        self, broker, monkeypatch,
    ) -> None:
        """SC-12: OSError on returncode-read → degrade open (alive, WARNING logged)."""
        monkeypatch.setenv("CLAUDE_CREW_LIVENESS_POLL_SECONDS", "0.1")

        # Create a transport whose _process.returncode raises on access.
        class _BadProcess:
            @property
            def returncode(self) -> int:  # type: ignore[override]
                raise OSError("transport probe broken")

        fake = ProgrammableSDKClient(scripted_responses=[])
        # Inject bad transport BEFORE patch so the poll loop reads it.
        fake._transport = types.SimpleNamespace(_process=_BadProcess())
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )

        # Allow 3+ poll cycles to elapse (3 * 0.1s = 0.3s; give margin).
        await asyncio.sleep(0.6)

        # Teammate must still be alive — probe errors degrade open, not dead.
        info = broker._info.get(tid)
        assert info is not None, "teammate info missing"
        assert info.alive, (
            "teammate should stay alive when probe raises OSError (D5 degrade-open)"
        )


# ---------- T3a hook callbacks (F8 tool-execution telemetry) ----------


class TestToolExecutionHooks:
    """BDD scenarios for PreToolUse / PostToolUse / PostToolUseFailure hooks (T3a)."""

    async def test_pre_tool_use_populates_current_tools(
        self, broker, monkeypatch
    ) -> None:
        """SC-1: PreToolUse populates current_tools in status snapshot."""
        from claude_crew.redaction import REDACTION_VERSION

        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        # Manually fire PreToolUse hook to avoid needing real SDK.
        await asyncio.sleep(0.1)  # Let teammate initialize.
        fake.set_hooks(
            {
                "PreToolUse": getattr(
                    teammate.options, "hooks", {}
                ).get("PreToolUse", [])
                if hasattr(teammate, "options")
                else [],
            }
        )

        # Actually, we can't access options from the outside. Instead, construct
        # a minimal test that fires the hook directly on the teammate object.
        # The hook is a bound method, so we call it directly.
        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        tool_use_id = "tu-1"
        await teammate._on_pre_tool_use(hook_input, tool_use_id, {})

        # Verify the entry was added.
        snap = teammate.status_snapshot()
        assert snap["current_tool_count"] == 1
        assert snap["current_tool"] == "Bash"
        assert snap["current_tools"][0]["tool_name"] == "Bash"
        assert snap["current_tools"][0]["tool_use_id"] == "tu-1"
        # Bash is on allowlist, so args_summary should be present.
        assert snap["current_tools"][0]["args_summary"] is not None
        assert "command=" in snap["current_tools"][0]["args_summary"]
        assert snap["redaction_version"] == REDACTION_VERSION

    async def test_post_tool_use_clears_and_sets_last_completed(
        self, broker, monkeypatch
    ) -> None:
        """SC-2: PostToolUse clears current_tools and sets last_tool_completed."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        # Pre-populate via _on_pre_tool_use.
        started_time = time.time()
        hook_input_pre = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        tool_use_id = "tu-1"
        await teammate._on_pre_tool_use(hook_input_pre, tool_use_id, {})

        assert teammate.status_snapshot()["current_tool_count"] == 1

        # Now fire PostToolUse.
        await asyncio.sleep(0.01)  # Ensure duration > 0.
        hook_input_post = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_response": "success",
        }
        await teammate._on_post_tool_use(hook_input_post, tool_use_id, {})

        snap = teammate.status_snapshot()
        assert snap["current_tool_count"] == 0
        assert snap["current_tool"] is None
        assert snap["last_tool_completed"] is not None
        assert snap["last_tool_completed"]["tool_name"] == "Bash"
        assert snap["last_tool_completed"]["outcome"] == "ok"
        assert snap["last_tool_completed"]["duration_seconds"] >= 0.0
        assert snap["last_tool_completed"]["error_summary"] is None

    async def test_post_tool_use_failure_outcome_interrupted(
        self, broker, monkeypatch
    ) -> None:
        """SC-3: PostToolUseFailure with is_interrupt=true → outcome='interrupted'."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        hook_input_pre = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        tool_use_id = "tu-1"
        await teammate._on_pre_tool_use(hook_input_pre, tool_use_id, {})

        # Fire PostToolUseFailure with is_interrupt=true.
        hook_input_failure = {
            "agent_id": None,
            "tool_name": "Bash",
            "is_interrupt": True,
            "error": "interrupted by user",
        }
        await teammate._on_post_tool_use_failure(hook_input_failure, tool_use_id, {})

        snap = teammate.status_snapshot()
        assert snap["last_tool_completed"]["outcome"] == "interrupted"

    async def test_post_tool_use_failure_outcome_failed(
        self, broker, monkeypatch
    ) -> None:
        """SC-3b: PostToolUseFailure with is_interrupt=false → outcome='failed'."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        hook_input_pre = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        tool_use_id = "tu-1"
        await teammate._on_pre_tool_use(hook_input_pre, tool_use_id, {})

        # Fire PostToolUseFailure with is_interrupt=false.
        hook_input_failure = {
            "agent_id": None,
            "tool_name": "Bash",
            "is_interrupt": False,
            "error": "exit 1",
        }
        await teammate._on_post_tool_use_failure(hook_input_failure, tool_use_id, {})

        snap = teammate.status_snapshot()
        assert snap["last_tool_completed"]["outcome"] == "failed"

    async def test_hooks_stamp_activity(
        self, broker, monkeypatch
    ) -> None:
        """SC-4: Hook callbacks stamp activity (last_activity_wallclock advances)."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        initial_activity = teammate._last_activity_wallclock
        await asyncio.sleep(0.05)

        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        await teammate._on_pre_tool_use(hook_input, "tu-1", {})

        # Activity should have advanced.
        assert teammate._last_activity_wallclock > initial_activity

    async def test_subagent_stamps_activity_and_updates_subagent_state(
        self, broker, monkeypatch
    ) -> None:
        """SC-10/D3: Subagent tool call stamps activity, updates _subagent_uses (SC-12: _tool_uses untouched)."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        initial_activity = teammate._last_activity_wallclock
        await asyncio.sleep(0.05)

        # Subagent call (agent_id is not None).
        hook_input = {
            "agent_id": "sub-1",
            "agent_type": "echo-runner",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        await teammate._on_pre_tool_use(hook_input, "tu-sub-1", {})

        # Activity should have advanced.
        assert teammate._last_activity_wallclock > initial_activity
        # SC-12: _tool_uses and current_tools are completely unaffected by subagent events.
        assert teammate.status_snapshot()["current_tool_count"] == 0
        assert teammate._tool_uses == {}
        # Subagent state is populated in the F7 namespace.
        assert "tu-sub-1" in teammate._subagent_uses
        assert teammate._subagent_uses["tu-sub-1"].agent_id == "sub-1"

    async def test_pre_twice_last_write_wins(
        self, broker, monkeypatch
    ) -> None:
        """SC-9: Pre-twice for same tool_use_id → last-write-wins + WARNING."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": {"command": "pytest"},
        }
        tool_use_id = "tu-1"

        # First Pre.
        t1 = time.time()
        await teammate._on_pre_tool_use(hook_input, tool_use_id, {})
        first_started = teammate._tool_uses[tool_use_id].started_at_wallclock

        # Wait a bit, then second Pre (same tool_use_id).
        await asyncio.sleep(0.05)
        t2 = time.time()
        await teammate._on_pre_tool_use(hook_input, tool_use_id, {})
        second_started = teammate._tool_uses[tool_use_id].started_at_wallclock

        # Second should have overwritten first.
        assert second_started > first_started
        assert len(teammate._tool_uses) == 1

    async def test_post_unknown_tool_use_warning_and_audit(
        self, broker, monkeypatch
    ) -> None:
        """SC-9/D8: Post for unknown tool_use_id NOT recently closed → WARNING + audit transcript."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        # Fire PostToolUse for a tool_use_id that was never Pre'd.
        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
        }
        await teammate._on_post_tool_use(hook_input, "tu-unknown", {})

        # current_tools and last_tool_completed should be unchanged (no Pre was fired).
        snap = teammate.status_snapshot()
        assert snap["current_tool_count"] == 0
        assert snap["last_tool_completed"] is None

    async def test_late_post_after_abandon_suppressed(
        self, broker, monkeypatch
    ) -> None:
        """SC-9/D8 fifth guard: Late Post for recently-closed tool_use_id → suppressed + INFO."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        tool_use_id = "tu-1"

        # Simulate a tool being abandoned by _close_open_tools.
        teammate._recently_closed_tool_use_ids.append(tool_use_id)

        # Now fire a late PostToolUse for that closed tool.
        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
        }
        await teammate._on_post_tool_use(hook_input, tool_use_id, {})

        # Nothing should have changed.
        snap = teammate.status_snapshot()
        assert snap["current_tool_count"] == 0
        assert snap["last_tool_completed"] is None

    async def test_hook_exception_does_not_crash(
        self, broker, monkeypatch
    ) -> None:
        """SC-8.2: Hook callback raise does not crash teammate."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        teammate = broker._teammates[tid]

        # Manually raise inside a hook by passing bad data.
        # The hook should catch this and return {} safely.
        hook_input = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_input": None,  # Invalid, but hook should not crash.
        }
        result = await teammate._on_pre_tool_use(hook_input, "tu-1", {})

        # Hook should return {} without raising.
        assert result == {}

    async def test_session_id_uses_crew_teammate_format(
        self, broker, monkeypatch
    ) -> None:
        """SC-16/D5: session_id uses f'{crew_id}-{teammate_id}' format."""
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hello",
        ))
        await _wait_for_lead_messages(broker, 1)

        # Check the query session_id that was recorded.
        recorded_query = fake.queries_received[0]
        expected_session_id = f"{broker.crew_id}-{tid}"
        assert recorded_query[1] == expected_session_id
        assert recorded_query[1] != "default"


# ---------- F7: subagent-activity envelope BDD scenarios ----------


class TestSubagentActivityEnvelopes:
    """BDD scenarios for Feature #7 subagent-tracking fields (T1)."""

    def _make_teammate(self) -> SdkTeammate:
        """Return a fresh SdkTeammate without spawning it in a broker."""
        return SdkTeammate(id="tm-f7", name="Builder", role="builder")

    # SC1: SdkTeammate initializes subagent namespace fields
    def test_subagent_fields_initialized(self) -> None:
        """SC1: Fresh SdkTeammate has correct subagent namespace field defaults."""
        import collections as col
        tm = self._make_teammate()

        assert tm._subagent_uses == {}
        assert tm._closed_subagent_scratch == {}
        assert isinstance(tm._recently_closed_subagent_use_ids, col.deque)
        assert tm._recently_closed_subagent_use_ids.maxlen == 64
        assert len(tm._recently_closed_subagent_use_ids) == 0
        assert tm._last_subagent_completed is None
        assert tm._task_notifs_by_tool_use_id == {}

    # SC2: status_snapshot includes subagent fields with no subagents
    def test_status_snapshot_includes_subagent_fields_empty(self) -> None:
        """SC2: status_snapshot includes F7 fields with empty values when no subagents."""
        tm = self._make_teammate()
        snap = tm.status_snapshot()

        assert snap["current_subagents"] == []
        assert snap["last_subagent_completed"] is None
        assert snap["in_flight_subagents_at_death"] is None
        # Existing F6/F8 fields must still be present.
        assert "last_activity_at_wallclock" in snap
        assert "current_turn_started_at_wallclock" in snap
        assert "idle_seconds" in snap
        assert "current_tools" in snap
        assert "current_tool" in snap
        assert "current_tool_count" in snap
        assert "last_tool_completed" in snap
        assert "redaction_version" in snap

    # SC3: status_snapshot reflects in-flight subagents from _subagent_uses
    def test_status_snapshot_reflects_inflight_subagents(self) -> None:
        """SC3: current_subagents includes entries from _subagent_uses."""
        import time as t
        tm = self._make_teammate()

        now = t.time()
        entry1 = _SubagentUseEntry(
            agent_id="sub-a",
            tool_use_id="tu-sa-1",
            spawned_at_wallclock=now,
        )
        entry2 = _SubagentUseEntry(
            agent_id="sub-b",
            tool_use_id="tu-sa-2",
            spawned_at_wallclock=now + 0.1,
        )
        tm._subagent_uses["tu-sa-1"] = entry1
        tm._subagent_uses["tu-sa-2"] = entry2

        snap = tm.status_snapshot()

        assert len(snap["current_subagents"]) == 2
        agent_ids = {e["agent_id"] for e in snap["current_subagents"]}
        assert agent_ids == {"sub-a", "sub-b"}
        tool_use_ids = {e["tool_use_id"] for e in snap["current_subagents"]}
        assert tool_use_ids == {"tu-sa-1", "tu-sa-2"}
        # Each entry has the three required fields.
        for entry in snap["current_subagents"]:
            assert "agent_id" in entry
            assert "tool_use_id" in entry
            assert "spawned_at_wallclock" in entry
        # F8 fields unaffected — _tool_uses and current_tools are separate.
        assert snap["current_tools"] == []
        assert snap["current_tool_count"] == 0

    # SC4: status_snapshot includes scratch entries (limbo-state fix)
    def test_status_snapshot_includes_scratch_entries(self) -> None:
        """SC4: current_subagents includes entries from _closed_subagent_scratch."""
        import time as t
        tm = self._make_teammate()

        now = t.time()
        # _subagent_uses is empty; only the scratch has an entry.
        scratch_entry = _ClosedSubagentEntry(
            agent_id="sub-limbo",
            tool_use_id="tu-limbo-1",
            spawned_at_wallclock=now,
            finished_at_wallclock=now + 0.5,
            hook_outcome="ok",
        )
        tm._closed_subagent_scratch["tu-limbo-1"] = scratch_entry

        snap = tm.status_snapshot()

        assert len(snap["current_subagents"]) == 1
        assert snap["current_subagents"][0]["agent_id"] == "sub-limbo"
        assert snap["current_subagents"][0]["tool_use_id"] == "tu-limbo-1"
        # last_subagent_completed is still None — nothing populated it.
        assert snap["last_subagent_completed"] is None

    # SC5: _record_task_notif stores by tool_use_id
    def test_record_task_notif_stores_by_tool_use_id(self) -> None:
        """SC5: _record_task_notif stores the TaskNotificationMessage keyed by tool_use_id."""
        tm = self._make_teammate()

        tnm = TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id="task-1",
            status="completed",
            output_file="",
            summary="done",
            uuid="tn-sc5",
            session_id="default",
        )
        tm._record_task_notif("tu-1", tnm)

        assert "tu-1" in tm._task_notifs_by_tool_use_id
        assert tm._task_notifs_by_tool_use_id["tu-1"] is tnm


# ---------- T3: subagent hook extension BDD scenarios ----------


class TestSubagentHookExtensions:
    """BDD scenarios for Feature #7 Task 3: subagent Pre/Post hook tracking + JSONL emit."""

    def _make_teammate(self) -> SdkTeammate:
        return SdkTeammate(id="tm-t3", name="Builder", role="builder")

    def _make_fake_broker(self) -> tuple[Any, list[tuple[str, dict]]]:
        """Return (fake_broker, events_list) where events_list records write_tool_event calls."""
        events: list[tuple[str, dict]] = []

        class _FakeSink:
            def write_tool_event(self, event: str, fields: dict) -> None:
                events.append((event, fields))

        class _FakeBroker:
            _sink = _FakeSink()

        return _FakeBroker(), events

    def _make_tnm(
        self,
        status: str = "completed",
        task_id: str = "task-1",
        summary: str = "Done.",
        tool_use_id: str | None = None,
        uuid: str = "tn-1",
    ) -> TaskNotificationMessage:
        return TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id=task_id,
            status=status,  # type: ignore[arg-type]
            output_file="",
            summary=summary,
            uuid=uuid,
            session_id="default",
            tool_use_id=tool_use_id,
        )

    async def test_pre_tool_use_spawn_populates_subagent_uses(self) -> None:
        """SC1: PreToolUse with agent_id → _subagent_uses[tu] populated, subagent_spawn in transcript, _tool_uses empty."""
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        hook_input = {
            "agent_id": "sub-agent-1",
            "tool_name": "Task",
            "tool_input": {},
        }
        result = await tm._on_pre_tool_use(hook_input, "tu-spawn-1", {})

        assert result == {}
        # _subagent_uses populated.
        assert "tu-spawn-1" in tm._subagent_uses
        entry = tm._subagent_uses["tu-spawn-1"]
        assert entry.agent_id == "sub-agent-1"
        assert entry.tool_use_id == "tu-spawn-1"
        assert entry.spawned_at_wallclock > 0.0
        # subagent_spawn emitted in transcript.
        spawn_events = [e for e in events if e[0] == "subagent_spawn"]
        assert len(spawn_events) == 1
        fields = spawn_events[0][1]
        assert fields["teammate_id"] == "tm-t3"
        assert fields["agent_id"] == "sub-agent-1"
        assert fields["tool_use_id"] == "tu-spawn-1"
        # SC-12: _tool_uses is completely untouched.
        assert tm._tool_uses == {}
        snap = tm.status_snapshot()
        assert snap["current_tools"] == []
        assert snap["current_tool_count"] == 0

    async def test_post_tool_use_close_moves_to_scratch(self) -> None:
        """SC2: PostToolUse with agent_id → _subagent_uses empty, _closed_subagent_scratch populated, no subagent_result yet."""
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        # Pre-populate _subagent_uses.
        import time as _time
        now = _time.time()
        from claude_crew.sdk_teammate import _SubagentUseEntry
        tm._subagent_uses["tu-close-1"] = _SubagentUseEntry(
            agent_id="sub-close-1",
            tool_use_id="tu-close-1",
            spawned_at_wallclock=now,
        )

        hook_input = {
            "agent_id": "sub-close-1",
            "tool_name": "Task",
        }
        result = await tm._on_post_tool_use(hook_input, "tu-close-1", {})

        assert result == {}
        # Moved out of in-flight dict.
        assert "tu-close-1" not in tm._subagent_uses
        # Moved into scratch.
        assert "tu-close-1" in tm._closed_subagent_scratch
        scratch = tm._closed_subagent_scratch["tu-close-1"]
        assert scratch.agent_id == "sub-close-1"
        assert scratch.hook_outcome == "ok"
        # No subagent_result emitted yet — that happens in _end_turn.
        result_events = [e for e in events if e[0] == "subagent_result"]
        assert len(result_events) == 0

    def test_end_turn_emits_subagent_result_with_tnm(self) -> None:
        """SC3: _end_turn with scratch entry + matching TNM → subagent_result with outcome=ok, summary, tnm_missing=False."""
        import time as _time
        from claude_crew.sdk_teammate import _ClosedSubagentEntry
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        now = _time.time()
        tm._closed_subagent_scratch["tu-emit-1"] = _ClosedSubagentEntry(
            agent_id="sub-emit-1",
            tool_use_id="tu-emit-1",
            spawned_at_wallclock=now - 1.0,
            finished_at_wallclock=now,
            hook_outcome="ok",
        )
        tnm = self._make_tnm(status="completed", summary="Done.", tool_use_id="tu-emit-1", uuid="tn-emit-1")
        tm._task_notifs_by_tool_use_id["tu-emit-1"] = tnm

        tm._end_turn()

        result_events = [e for e in events if e[0] == "subagent_result"]
        assert len(result_events) == 1
        fields = result_events[0][1]
        assert fields["teammate_id"] == "tm-t3"
        assert fields["agent_id"] == "sub-emit-1"
        assert fields["tool_use_id"] == "tu-emit-1"
        assert fields["outcome"] == "ok"
        assert fields["summary"] == "Done."
        assert fields["tnm_missing"] is False
        assert fields["duration_seconds"] >= 0.0
        # Both dicts cleared after _end_turn.
        assert tm._closed_subagent_scratch == {}
        assert tm._task_notifs_by_tool_use_id == {}

    def test_end_turn_emits_subagent_result_tnm_missing(self) -> None:
        """SC4: _end_turn with scratch entry, no TNM → subagent_result with tnm_missing=True, outcome from hook."""
        import time as _time
        from claude_crew.sdk_teammate import _ClosedSubagentEntry
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        now = _time.time()
        tm._closed_subagent_scratch["tu-nomatch-1"] = _ClosedSubagentEntry(
            agent_id="sub-nomatch-1",
            tool_use_id="tu-nomatch-1",
            spawned_at_wallclock=now - 2.0,
            finished_at_wallclock=now,
            hook_outcome="ok",
        )
        # No TNM stored for this tool_use_id.

        tm._end_turn()

        result_events = [e for e in events if e[0] == "subagent_result"]
        assert len(result_events) == 1
        fields = result_events[0][1]
        assert fields["tnm_missing"] is True
        # Outcome falls back to hook_outcome.
        assert fields["outcome"] == "ok"
        assert fields["summary"] is None

    async def test_f8_invariants_sc12_tool_uses_unaffected(self) -> None:
        """SC5/SC-12: subagent Pre + Post fire → _tool_uses unchanged, current_tools == [], last_tool_completed unchanged."""
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        import time as _time
        hook_pre = {"agent_id": "sub-inv-1", "tool_name": "Task", "tool_input": {}}
        await tm._on_pre_tool_use(hook_pre, "tu-inv-1", {})
        hook_post = {"agent_id": "sub-inv-1", "tool_name": "Task"}
        await tm._on_post_tool_use(hook_post, "tu-inv-1", {})

        assert tm._tool_uses == {}
        snap = tm.status_snapshot()
        assert snap["current_tools"] == []
        assert snap["last_tool_completed"] is None

    async def test_parallel_fan_out_sc11(self) -> None:
        """SC6/SC-11: Two subagent Pre fires → _subagent_uses has both, status_snapshot has two entries."""
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        hook_pre_1 = {"agent_id": "sub-fanout-1", "tool_name": "Task", "tool_input": {}}
        hook_pre_2 = {"agent_id": "sub-fanout-2", "tool_name": "Task", "tool_input": {}}
        await tm._on_pre_tool_use(hook_pre_1, "tu-fanout-1", {})
        await tm._on_pre_tool_use(hook_pre_2, "tu-fanout-2", {})

        assert len(tm._subagent_uses) == 2
        assert "tu-fanout-1" in tm._subagent_uses
        assert "tu-fanout-2" in tm._subagent_uses
        snap = tm.status_snapshot()
        assert len(snap["current_subagents"]) == 2
        agent_ids = {e["agent_id"] for e in snap["current_subagents"]}
        assert agent_ids == {"sub-fanout-1", "sub-fanout-2"}

    async def test_hook_exception_isolation_sc13(self) -> None:
        """SC7/SC-13: Inject exception in D3 branch (write_tool_event raises) → hook returns {} without crashing."""
        tm = self._make_teammate()

        class _RaisingSink:
            def write_tool_event(self, event: str, fields: dict) -> None:
                raise RuntimeError("disk full")

        class _RaisingBroker:
            _sink = _RaisingSink()

        tm._broker = _RaisingBroker()  # type: ignore[assignment]

        hook_input = {"agent_id": "sub-exc-1", "tool_name": "Task", "tool_input": {}}
        result = await tm._on_pre_tool_use(hook_input, "tu-exc-1", {})

        # Hook must return {} and not raise even when JSONL write fails.
        assert result == {}
        # Because write_tool_event raised before dict was populated (F2 ordering),
        # _subagent_uses should NOT have the entry.
        assert "tu-exc-1" not in tm._subagent_uses

    async def test_close_open_subagents_in_flight_sc8(self) -> None:
        """SC8/SC-8: Two entries in _subagent_uses → subagent_abandoned_batch emitted, _subagent_uses cleared."""
        import time as _time
        from claude_crew.sdk_teammate import _SubagentUseEntry
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        now = _time.time()
        tm._subagent_uses["tu-ab-1"] = _SubagentUseEntry(agent_id="sub-ab-1", tool_use_id="tu-ab-1", spawned_at_wallclock=now)
        tm._subagent_uses["tu-ab-2"] = _SubagentUseEntry(agent_id="sub-ab-2", tool_use_id="tu-ab-2", spawned_at_wallclock=now + 0.1)

        tm._close_open_subagents(reason="death")

        abandon_events = [e for e in events if e[0] == "subagent_abandoned_batch"]
        assert len(abandon_events) == 1
        fields = abandon_events[0][1]
        assert fields["teammate_id"] == "tm-t3"
        assert fields["reason"] == "death"
        assert fields["count"] == 2
        agent_ids = {s["agent_id"] for s in fields["subagents"]}
        assert agent_ids == {"sub-ab-1", "sub-ab-2"}
        assert tm._subagent_uses == {}

    def test_close_open_subagents_scratch_only_sentinel_f1(self) -> None:
        """SC9/sentinel F1: _subagent_uses empty, one entry in _closed_subagent_scratch → subagent_abandoned_batch emitted."""
        import time as _time
        from claude_crew.sdk_teammate import _ClosedSubagentEntry
        tm = self._make_teammate()
        broker, events = self._make_fake_broker()
        tm._broker = broker  # type: ignore[assignment]

        now = _time.time()
        tm._closed_subagent_scratch["tu-scratch-1"] = _ClosedSubagentEntry(
            agent_id="sub-scratch-1",
            tool_use_id="tu-scratch-1",
            spawned_at_wallclock=now - 1.0,
            finished_at_wallclock=now,
            hook_outcome="ok",
        )

        tm._close_open_subagents(reason="kill")

        abandon_events = [e for e in events if e[0] == "subagent_abandoned_batch"]
        assert len(abandon_events) == 1
        fields = abandon_events[0][1]
        assert fields["reason"] == "kill"
        assert fields["count"] == 1
        assert fields["subagents"][0]["agent_id"] == "sub-scratch-1"
        assert tm._closed_subagent_scratch == {}


class TestCollectResponseTextT2:
    """BDD scenarios for Feature #7 Task 2: _collect_response_text + TurnDrainResult."""

    def _make_tnm(
        self,
        status: str,
        task_id: str = "task-1",
        summary: str = "done",
        tool_use_id: str | None = None,
        uuid: str = "tn-1",
    ) -> TaskNotificationMessage:
        return TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id=task_id,
            status=status,  # type: ignore[arg-type]
            output_file="",
            summary=summary,
            uuid=uuid,
            session_id="default",
            tool_use_id=tool_use_id,
        )

    async def test_failed_task_notifs_accumulates_failed_and_stopped(self) -> None:
        """SC-T2-1: _collect_response_text accumulates failed/stopped TNMs into list.

        Given fake client yields two TNMs (failed, stopped) and one text block;
        When called with no callback;
        Then result.failed_task_notifs has two entries; result.text is the text content.
        """
        tnm_failed = self._make_tnm("failed", task_id="t1", summary="boom", uuid="tn-f")
        tnm_stopped = self._make_tnm("stopped", task_id="t2", summary="halt", uuid="tn-s")

        class _FakeClient:
            async def receive_response(self):
                yield tnm_failed
                yield AssistantMessage(content=[TextBlock(text="parent said ok")], model="fake")
                yield tnm_stopped

        result = await _collect_response_text(_FakeClient())

        assert isinstance(result, TurnDrainResult)
        assert result.text == "parent said ok"
        assert len(result.failed_task_notifs) == 2
        assert result.failed_task_notifs[0] is tnm_failed
        assert result.failed_task_notifs[1] is tnm_stopped

    async def test_record_task_notif_callback_fires_for_all_statuses(self) -> None:
        """SC-T2-2: record_task_notif callback fires for all TNM statuses.

        Given client yields TNMs with statuses: completed, failed, stopped;
        And a callback recording (tool_use_id, tnm) pairs;
        When _collect_response_text called with that callback;
        Then callback called three times; failed_task_notifs has two (failed, stopped only).
        """
        tnm_completed = self._make_tnm("completed", task_id="t1", uuid="tn-c", tool_use_id="tu-1")
        tnm_failed = self._make_tnm("failed", task_id="t2", uuid="tn-f", tool_use_id="tu-2")
        tnm_stopped = self._make_tnm("stopped", task_id="t3", uuid="tn-s", tool_use_id="tu-3")

        class _FakeClient:
            async def receive_response(self):
                yield tnm_completed
                yield tnm_failed
                yield tnm_stopped

        recorded: list[tuple[str, TaskNotificationMessage]] = []

        def capture(tool_use_id: str, tnm: TaskNotificationMessage) -> None:
            recorded.append((tool_use_id, tnm))

        result = await _collect_response_text(_FakeClient(), record_task_notif=capture)

        # Callback fires for all 3 statuses
        assert len(recorded) == 3
        assert recorded[0] == ("tu-1", tnm_completed)
        assert recorded[1] == ("tu-2", tnm_failed)
        assert recorded[2] == ("tu-3", tnm_stopped)

        # Only failed+stopped go into failed_task_notifs
        assert len(result.failed_task_notifs) == 2
        assert result.failed_task_notifs[0] is tnm_failed
        assert result.failed_task_notifs[1] is tnm_stopped

    async def test_record_task_notif_skips_tnm_with_null_tool_use_id(self) -> None:
        """SC-T2-3: record_task_notif callback is NOT called when tool_use_id is None.

        Given TNM with tool_use_id=None;
        When callback would otherwise fire;
        Then callback NOT called for that TNM.
        TNM still counted in failed_task_notifs if status is failed.
        """
        tnm_no_id = self._make_tnm("failed", task_id="t1", uuid="tn-noid", tool_use_id=None)
        tnm_with_id = self._make_tnm("completed", task_id="t2", uuid="tn-withid", tool_use_id="tu-x")

        class _FakeClient:
            async def receive_response(self):
                yield tnm_no_id
                yield tnm_with_id

        recorded: list[tuple[str, TaskNotificationMessage]] = []

        def capture(tool_use_id: str, tnm: TaskNotificationMessage) -> None:
            recorded.append((tool_use_id, tnm))

        result = await _collect_response_text(_FakeClient(), record_task_notif=capture)

        # Only the TNM with a tool_use_id triggers the callback
        assert len(recorded) == 1
        assert recorded[0] == ("tu-x", tnm_with_id)

        # failed TNM (null tool_use_id) still counted in failed_task_notifs
        assert len(result.failed_task_notifs) == 1
        assert result.failed_task_notifs[0] is tnm_no_id

    async def test_handle_one_turn_synthesis_uses_failed_task_notifs(
        self, broker, monkeypatch,
    ) -> None:
        """SC-T2-4: _handle_one_turn synthesis uses failed_task_notifs.

        Given turn produces empty text and one failed TNM;
        When _handle_one_turn completes;
        Then lead receives invalid_response envelope with TNM summary.
        """
        tnm_failed = self._make_tnm(
            "failed", task_id="t-synth", summary="builder exploded", uuid="tn-synth",
        )

        fake = FakeSDKClient(scripted_responses=[
            [tnm_failed],  # stream yields only a failed TNM, no text
        ])
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="go",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=2.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "invalid_response"
        assert "builder exploded" in msgs[0].payload.get("message", "")


# ---------- Feature #10: role-field extraction ----------


class TestRoleFieldExtraction:
    """BDD scenarios for Feature #10 Task 2: cwd/permission_mode params + role-pack field extraction."""

    async def test_permission_mode_from_role_pack(self, broker, monkeypatch) -> None:
        """Test: Agent defined with permissionMode in pack → ClaudeAgentOptions gets permission_mode."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test builder",
            prompt="be a builder",
            model="claude-haiku-4-5-20251001",
            tools=["Read"],
            permissionMode="bypassPermissions",
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={"builder": agent_def}
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.permission_mode == "bypassPermissions"

    async def test_spawn_time_permission_mode_overrides_role_pack(
        self, broker, monkeypatch,
    ) -> None:
        """Test: Spawn-time permission_mode wins over role-pack permissionMode."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test builder",
            prompt="be a builder",
            model="claude-haiku-4-5-20251001",
            tools=["Read"],
            permissionMode="default",
        )

        def factory(id, name, role, **_kwargs):
            # Spawn-time permission_mode wins over role-pack permissionMode.
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={"builder": agent_def},
                permission_mode="plan",  # This should override role-pack's "default"
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.permission_mode == "plan"

    async def test_spawn_time_none_falls_back_to_role_pack(
        self, broker, monkeypatch,
    ) -> None:
        """Test: Spawn-time permission_mode=None falls back to role-pack permissionMode."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test builder",
            prompt="be a builder",
            model="claude-haiku-4-5-20251001",
            tools=["Read"],
            permissionMode="bypassPermissions",
        )

        def factory(id, name, role, **_kwargs):
            # permission_mode=None (the default), so falls back to role-pack permissionMode.
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={"builder": agent_def},
                permission_mode=None,
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.permission_mode == "bypassPermissions"

    async def test_skills_from_role_pack(self, broker, monkeypatch) -> None:
        """Test: Agent with skills in pack → ClaudeAgentOptions gets skills."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test builder",
            prompt="be a builder",
            model="claude-haiku-4-5-20251001",
            tools=["Read"],
            skills=["sdd-workflow"],
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={"builder": agent_def}
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.skills == ["sdd-workflow"]

    async def test_disallowed_tools_from_role_pack(self, broker, monkeypatch) -> None:
        """Test: Agent with disallowedTools in pack → ClaudeAgentOptions gets disallowed_tools."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test builder",
            prompt="be a builder",
            model="claude-haiku-4-5-20251001",
            tools=["Read"],
            disallowedTools=["Bash", "WebFetch"],
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={"builder": agent_def}
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.disallowed_tools == ["Bash", "WebFetch"]

    async def test_cwd_reaches_options(self, broker, monkeypatch) -> None:
        """Test: SdkTeammate(cwd=...) passes cwd to ClaudeAgentOptions."""
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                cwd="/tmp/test-proj",
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        # cwd can be str or Path, so convert to str for comparison.
        assert str(opts.cwd) == "/tmp/test-proj"

    async def test_unknown_role_does_not_fail(self, broker, monkeypatch) -> None:
        """Test: Unknown role doesn't crash; no fields extracted."""
        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={},  # empty pack
            )

        tid = await broker.spawn_teammate(
            role="nonexistent", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        # No role-level fields set, SDK defaults apply.
        assert opts.permission_mode is None
        assert opts.skills is None
        # disallowed_tools defaults to empty list in SDK.
        assert opts.disallowed_tools == [] or opts.disallowed_tools is None

    async def test_role_fields_none_not_applied(self, broker, monkeypatch) -> None:
        """Test: Role fields that are None in the AgentDefinition are not applied."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test builder",
            prompt="be a builder",
            model="claude-haiku-4-5-20251001",
            tools=["Read"],
            # Explicitly no permissionMode, skills, disallowedTools set
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role,
                agents={"builder": agent_def}
            )

        tid = await broker.spawn_teammate(
            role="builder", name=None, factory=factory,
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        # Fields should not be explicitly set, SDK defaults apply.
        assert opts.permission_mode is None
        assert opts.skills is None
        assert opts.disallowed_tools == [] or opts.disallowed_tools is None


# ---------- F14: token/cost telemetry ----------


class TestTokenCostTelemetry:
    """Tests for Feature #14: SdkTeammate ResultMessage-based token/cost capture.

    All tests use text_response_with_usage() to script ResultMessage values.
    D-9 contract: tests using text_response() (no usage) still pass — those
    teammates' fields stay at zero.
    """

    async def test_token_cost_overwrite_from_result_message_only(
        self, broker, monkeypatch
    ) -> None:
        """D-1: cost AND tokens come from ResultMessage only, not AssistantMessage.usage.

        Script an AssistantMessage with usage-like content in its content list
        and a ResultMessage with distinct values. Assert the teammate stores the
        ResultMessage values, not the AssistantMessage values.
        """
        # AssistantMessage has no `.usage` field in our fake — it's driven by
        # TextBlock content only. The key assertion is that the snapshot values
        # match the ResultMessage, proving we never consulted AssistantMessage.
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "hello",
                    turn_input_tokens=500,
                    turn_output_tokens=100,
                    cumulative_cost_usd=0.25,
                )
            ]
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)

        snap = broker._teammates[tid].status_snapshot()
        # Per-turn values accumulated; cost from ResultMessage.
        assert snap["total_input_tokens"] == 500
        assert snap["total_output_tokens"] == 100
        assert snap["total_cost_usd"] == 0.25

    async def test_three_turns_show_final_cumulative_not_sum(
        self, broker, monkeypatch
    ) -> None:
        """D-2: per-turn tokens accumulate; cost overwrites (session-cumulative from SDK).

        Three turns with per-turn tokens (100/50) → (300/150) → (600/300),
        but cost is cumulative: 0.10 → 0.30 → 0.60.
        After all three, snap["total_cost_usd"] must be 0.60 (overwrite, not sum).
        After all three, snap["total_input_tokens"] must be 100+300+600=1000 (accumulate).
        """
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "turn1",
                    turn_input_tokens=100,
                    turn_output_tokens=50,
                    cumulative_cost_usd=0.10,
                ),
                text_response_with_usage(
                    "turn2",
                    turn_input_tokens=300,
                    turn_output_tokens=150,
                    cumulative_cost_usd=0.30,
                ),
                text_response_with_usage(
                    "turn3",
                    turn_input_tokens=600,
                    turn_output_tokens=300,
                    cumulative_cost_usd=0.60,
                ),
            ]
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        for i in range(3):
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0,
                payload=f"q{i}",
            ))
        await _wait_for_lead_messages(broker, 3)

        snap = broker._teammates[tid].status_snapshot()
        # Cost: overwrite semantics — final cumulative value (0.60, not 0.10+0.30+0.60).
        assert snap["total_cost_usd"] == 0.60, (
            f"expected 0.60 (overwrite), got {snap['total_cost_usd']} "
            f"(if 1.00, that's accumulate-not-overwrite — D-2 violated)"
        )
        # Tokens: accumulate per-turn deltas across turns.
        assert snap["total_input_tokens"] == 1000, (
            f"expected 1000 (100+300+600), got {snap['total_input_tokens']}"
        )
        assert snap["total_output_tokens"] == 500, (
            f"expected 500 (50+150+300), got {snap['total_output_tokens']}"
        )

    async def test_cache_tokens_summed_into_total_input(
        self, broker, monkeypatch
    ) -> None:
        """D-3: total_input_tokens = input_tokens + cache_read + cache_creation.

        Script ResultMessage with input_tokens=100, output_tokens=50,
        cache_read_input_tokens=1000. Assert snap["total_input_tokens"] == 1100.
        """
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "cached turn",
                    turn_input_tokens=100,  # per-turn net input tokens
                    turn_output_tokens=50,
                    cumulative_cost_usd=0.05,
                    cache_read_input_tokens=1000,  # cache tokens added in usage dict
                )
            ]
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)

        snap = broker._teammates[tid].status_snapshot()
        assert snap["total_input_tokens"] == 1100, (
            f"expected 1100 (100 net + 1000 cache_read), got {snap['total_input_tokens']}"
        )
        assert snap["total_output_tokens"] == 50

    async def test_token_totals_accumulate_across_turns_even_when_per_turn_decreases(
        self, broker, monkeypatch
    ) -> None:
        """Regression: per-turn tokens accumulate monotonically even when per-turn value decreases.

        This test would have FAILED under the old overwrite-semantics bug:
          - Turn 1: 100 input → snap["total_input_tokens"] = 100
          - Turn 2: 200 input → snap["total_input_tokens"] = 300 (accumulate)
          - Turn 3: 50 input → snap["total_input_tokens"] = 350 (still accumulate, despite per-turn decrease)

        Bug: under overwrite semantics, turn 3 would set it to 50 (wrong).

        Cost uses cumulative values (overwrite):
          - Turn 1: 0.0001 cumulative → snap["total_cost_usd"] = 0.0001
          - Turn 2: 0.0002 cumulative → snap["total_cost_usd"] = 0.0002
          - Turn 3: 0.0003 cumulative → snap["total_cost_usd"] = 0.0003 (overwrite)

        Live evidence that triggered this fix: a teammate over 2 turns showed
        input tokens 28,541 → 14,580 (decrease) with cost increasing monotonically,
        proving tokens are per-turn while cost is cumulative.
        """
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "turn1",
                    turn_input_tokens=100,
                    turn_output_tokens=50,
                    cumulative_cost_usd=0.0001,
                ),
                text_response_with_usage(
                    "turn2",
                    turn_input_tokens=200,
                    turn_output_tokens=80,
                    cumulative_cost_usd=0.0002,
                ),
                text_response_with_usage(
                    "turn3",
                    turn_input_tokens=50,  # SMALLER than turn 2 — would have failed old overwrite
                    turn_output_tokens=30,
                    cumulative_cost_usd=0.0003,
                ),
            ]
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        for i in range(3):
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0,
                payload=f"q{i}",
            ))
        await _wait_for_lead_messages(broker, 3)

        snap = broker._teammates[tid].status_snapshot()
        # Tokens accumulate: 100 + 200 + 50 = 350 (monotonic increase)
        assert snap["total_input_tokens"] == 350, (
            f"expected 350 (100+200+50 accumulate); got {snap['total_input_tokens']} "
            f"(overwrite bug: would be 50)"
        )
        assert snap["total_output_tokens"] == 160, (
            f"expected 160 (50+80+30 accumulate); got {snap['total_output_tokens']}"
        )
        # Cost overwrites: final cumulative is 0.0003 (not sum of 0.0001+0.0002+0.0003)
        assert snap["total_cost_usd"] == 0.0003, (
            f"expected 0.0003 (cumulative overwrite); got {snap['total_cost_usd']}"
        )

    async def test_malformed_result_message_leaves_totals_unchanged(
        self, broker, monkeypatch, caplog
    ) -> None:
        """D-8: malformed usage data leaves totals unchanged; WARNING is logged.

        Turn 1 emits valid ResultMessage (cost 0.50).
        Turn 2 emits ResultMessage with usage="not-a-dict" (malformed).
        Assert snap["total_cost_usd"] == 0.50 after turn 2.
        Assert WARNING was logged for the malformed turn.
        """
        import logging

        # Turn 2: malformed ResultMessage — usage is a string, not a dict.
        malformed_rm = ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=2,
            session_id="fake",
            total_cost_usd=0.99,  # cost also present but usage is bad
            usage="not-a-dict",  # type: ignore[arg-type]
        )
        from claude_agent_sdk.types import AssistantMessage, TextBlock
        malformed_turn = [
            AssistantMessage(content=[TextBlock(text="turn2")], model="fake-model"),
            malformed_rm,
        ]

        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "turn1",
                    turn_input_tokens=200,
                    turn_output_tokens=80,
                    cumulative_cost_usd=0.50,
                ),
                malformed_turn,
            ]
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        for i in range(2):
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0,
                payload=f"q{i}",
            ))
        await _wait_for_lead_messages(broker, 2)

        snap = broker._teammates[tid].status_snapshot()
        # Token totals unchanged (malformed usage) but cost DID update (cost itself valid).
        # Per D-8: malformed-per-field, not transactional. usage extraction failed
        # (tokens stay at turn-1 values) BUT total_cost_usd was structurally valid
        # on the same ResultMessage so cost DID update to 0.99.
        assert snap["total_input_tokens"] == 200, (
            f"token totals should be unchanged from turn 1 (malformed usage); "
            f"got {snap['total_input_tokens']}"
        )
        assert snap["total_output_tokens"] == 80
        assert snap["total_cost_usd"] == 0.99, (
            f"cost should have updated from valid total_cost_usd field on the "
            f"malformed ResultMessage (D-8 per-field independence); "
            f"got {snap['total_cost_usd']}"
        )

        # A WARNING must have been logged about the malformed usage.
        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "not a dict" in r.message
        ]
        assert warning_records, (
            "Expected WARNING log about malformed usage (not a dict), "
            f"got records: {[r.message for r in caplog.records if r.levelno == logging.WARNING]}"
        )

    async def test_snapshot_excludes_in_flight_turn(
        self, broker, monkeypatch
    ) -> None:
        """SC-2: snapshot during in-flight turn shows pre-turn values only.

        We run turn 1 to establish a baseline (cost=0.10, tokens=100/50).
        Then start turn 2 with a response_hangs=True client — the stream never
        delivers a ResultMessage. We read the snapshot mid-turn and assert it
        still shows the turn-1 values, not any in-flight contribution.

        The hang simulates "query sent, ResultMessage not yet received."
        We cancel the teammate after checking the snapshot.
        """
        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "turn1",
                    turn_input_tokens=100,
                    turn_output_tokens=50,
                    cumulative_cost_usd=0.10,
                ),
                # turn 2 is scripted but response_hangs makes it block
                text_response_with_usage(
                    "turn2",
                    turn_input_tokens=200,  # per-turn: 300 total = 100 + 200
                    turn_output_tokens=100,  # per-turn: 150 total = 50 + 100
                    cumulative_cost_usd=0.30,
                ),
            ],
            response_hangs=[False, True],
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )

        # Complete turn 1.
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="q1",
        ))
        await _wait_for_lead_messages(broker, 1)

        # Verify baseline from turn 1.
        snap_after_turn1 = broker._teammates[tid].status_snapshot()
        assert snap_after_turn1["total_cost_usd"] == 0.10

        # Start turn 2 (will hang — no ResultMessage arrives).
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="q2",
        ))
        # Brief yield to let the teammate enter the hung receive_response() loop.
        await asyncio.sleep(0.05)

        # Snapshot mid-turn must still show turn-1 values.
        snap_mid_turn = broker._teammates[tid].status_snapshot()
        assert snap_mid_turn["total_cost_usd"] == 0.10, (
            f"snapshot mid-turn should show 0.10 (turn-1 value), "
            f"got {snap_mid_turn['total_cost_usd']}"
        )
        assert snap_mid_turn["total_input_tokens"] == 100
        assert snap_mid_turn["total_output_tokens"] == 50

    async def test_token_cost_types_and_no_scinotation(
        self, broker, monkeypatch
    ) -> None:
        """SC-9: total_input/output_tokens are int; total_cost_usd is float.
        JSON serialization must not produce scientific notation.

        Python's json.dumps uses repr(float) which switches to scientific
        notation for values below ~1e-5. We verify the snap types AND that
        json.dumps of the snap dict produces no 'e' in the cost value.
        """
        import json

        fake = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "tiny cost",
                    turn_input_tokens=10,
                    turn_output_tokens=5,
                    cumulative_cost_usd=0.0001,
                )
            ]
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)

        snap = broker._teammates[tid].status_snapshot()

        # Type assertions.
        assert isinstance(snap["total_input_tokens"], int), (
            f"total_input_tokens must be int, got {type(snap['total_input_tokens'])}"
        )
        assert isinstance(snap["total_output_tokens"], int), (
            f"total_output_tokens must be int, got {type(snap['total_output_tokens'])}"
        )
        assert isinstance(snap["total_cost_usd"], float), (
            f"total_cost_usd must be float, got {type(snap['total_cost_usd'])}"
        )

        # Value assertions.
        assert snap["total_input_tokens"] == 10
        assert snap["total_output_tokens"] == 5
        assert snap["total_cost_usd"] == 0.0001

        # JSON serialization must not produce scientific notation for 0.0001.
        # Python's json.dumps will produce "0.0001" for this value (above 1e-5 threshold).
        serialized = json.dumps(snap["total_cost_usd"])
        assert "e" not in serialized.lower(), (
            f"json.dumps of total_cost_usd={snap['total_cost_usd']!r} "
            f"produced scientific notation: {serialized!r}"
        )


# ---------------------------------------------------------------------------
# Feature #19 T2: hook append sites for completed_tool_events
# ---------------------------------------------------------------------------


class TestF19CompletedToolEventsAppend:
    """T2: PostToolUse / PostToolUseFailure / orphan_post / Pre-only behavior."""

    async def test_completed_tool_events_appended_on_post_ok(
        self, broker, monkeypatch
    ) -> None:
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "ls /tmp"}},
            "tu-ok", {},
        )
        await asyncio.sleep(0.005)
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_response": "ok"},
            "tu-ok", {},
        )

        assert len(teammate._completed_tool_events) == 1
        ev = teammate._completed_tool_events[0]
        assert ev.tool_name == "Bash"
        assert ev.outcome == "ok"
        assert ev.tool_use_id == "tu-ok"
        assert ev.error_summary is None
        assert ev.args_summary is not None and "command=" in ev.args_summary
        assert ev.duration_seconds > 0

    async def test_completed_tool_events_appended_on_post_failed(
        self, broker, monkeypatch
    ) -> None:
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "false"}},
            "tu-fail", {},
        )
        await teammate._on_post_tool_use_failure(
            {"agent_id": None, "tool_name": "Bash", "error": "exit code 1"},
            "tu-fail", {},
        )

        assert len(teammate._completed_tool_events) == 1
        ev = teammate._completed_tool_events[0]
        assert ev.outcome == "failed"
        assert ev.error_summary is not None and "exit code 1" in ev.error_summary

    async def test_pre_tool_use_does_not_append_to_completed_events(
        self, broker, monkeypatch
    ) -> None:
        """SC-7 / sentinel D2: PreToolUse alone never reaches the completed-events deque."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "sleep 5"}},
            "tu-pending", {},
        )

        # PreToolUse populated the in-flight dict but NOT the completed-events deque.
        assert "tu-pending" in teammate._tool_uses
        assert len(teammate._completed_tool_events) == 0

    async def test_orphan_post_not_appended_to_completed_events(
        self, broker, monkeypatch
    ) -> None:
        """SC-3 / D-3: PostToolUse with no matching Pre writes JSONL orphan_post but skips deque."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        # Fire Post without prior Pre.
        await teammate._on_post_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_response": "ok"},
            "tu-orphan", {},
        )

        # Deque stays empty even though the orphan_post JSONL line was written.
        assert len(teammate._completed_tool_events) == 0

    async def test_completed_tool_events_appended_on_post_interrupted(
        self, broker, monkeypatch
    ) -> None:
        """Sentinel DEFER-2: is_interrupt routes through _on_post_common with outcome='interrupted'."""
        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        await teammate._on_pre_tool_use(
            {"agent_id": None, "tool_name": "Bash", "tool_input": {"command": "sleep 60"}},
            "tu-int", {},
        )
        await teammate._on_post_tool_use_failure(
            {"agent_id": None, "tool_name": "Bash", "is_interrupt": True, "error": "user stop"},
            "tu-int", {},
        )

        assert len(teammate._completed_tool_events) == 1
        ev = teammate._completed_tool_events[0]
        assert ev.outcome == "interrupted"
        assert ev.error_summary is not None

    async def test_completed_tool_events_appended_when_transcript_disabled(
        self, broker, monkeypatch
    ) -> None:
        """SC-8 / D-4: in-memory append must NOT be gated on JSONL write success."""
        from unittest.mock import MagicMock

        fake = ProgrammableSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)
        tid = await broker.spawn_teammate(role="r", name=None, factory=_factory_for(fake))
        teammate = broker._teammates[tid]

        # Make the transcript sink raise on write; the hook should still append to deque.
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

        # Deque has the event despite the JSONL failure.
        assert len(teammate._completed_tool_events) == 1
        assert teammate._completed_tool_events[0].tool_use_id == "tu-disk"


# ============================================================================
# Feature #17 T4 — _load_user_mcp_servers + _resolve_mcp_servers + _run wiring
# ============================================================================

import json as _json
import logging as _logging
from pathlib import Path as _Path

from claude_crew.sdk_teammate import (
    _load_user_mcp_servers,
    _resolve_mcp_servers,
)


def _write_claude_json(home: _Path, mcp_servers: dict | None) -> _Path:
    home.mkdir(parents=True, exist_ok=True)
    cfg: dict = {}
    if mcp_servers is not None:
        cfg["mcpServers"] = mcp_servers
    path = home / ".claude.json"
    path.write_text(_json.dumps(cfg))
    return path


class TestLoadUserMcpServersFullConfig:
    """Spawn-time helper: returns full {name: config} map; best-effort on failures."""

    def test_returns_full_configs(self, tmp_path: _Path) -> None:
        home = tmp_path / "home"
        _write_claude_json(home, {
            "atlassian": {"type": "http", "url": "https://x"},
            "claude-crew": {"type": "stdio", "command": "uv"},
        })
        result = _load_user_mcp_servers(home)
        assert result == {
            "atlassian": {"type": "http", "url": "https://x"},
            "claude-crew": {"type": "stdio", "command": "uv"},
        }

    def test_missing_file_returns_empty(self, tmp_path: _Path) -> None:
        assert _load_user_mcp_servers(tmp_path / "nonexistent") == {}

    def test_malformed_json_returns_empty(self, tmp_path: _Path) -> None:
        home = tmp_path / "home"
        home.mkdir(parents=True)
        (home / ".claude.json").write_text("{not valid")
        assert _load_user_mcp_servers(home) == {}

    def test_non_dict_server_config_filtered(self, tmp_path: _Path) -> None:
        """If a server's config is not a dict (malformed user file), filter it."""
        home = tmp_path / "home"
        home.mkdir(parents=True)
        (home / ".claude.json").write_text(_json.dumps({
            "mcpServers": {
                "good": {"type": "stdio"},
                "bad": "not-a-dict",
            }
        }))
        result = _load_user_mcp_servers(home)
        assert "good" in result
        assert "bad" not in result


class TestResolveMcpServers:
    """Unit tests for _resolve_mcp_servers (D-4, D-7, D-11, D-13)."""

    def test_inline_dict_with_name_strips_name_and_uses_as_key(
        self, tmp_path: _Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(_logging.INFO, logger="claude_crew.sdk_teammate"):
            result = _resolve_mcp_servers(
                [{"type": "stdio", "name": "local-x", "command": "uv"}],
                role="explorer", teammate_id="t-123", home_dir=tmp_path,
            )
        assert result == {"local-x": {"type": "stdio", "command": "uv"}}
        # D-13 breadcrumb
        info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
        assert any(
            "t-123" in m and "explorer" in m and "local-x" in m and "stdio" in m
            for m in info_msgs
        )

    def test_inline_dict_without_name_falls_back_to_type_index(self, tmp_path: _Path) -> None:
        result = _resolve_mcp_servers(
            [{"type": "stdio", "command": "uv"}, {"type": "http", "url": "x"}],
            role="r", teammate_id="t", home_dir=tmp_path,
        )
        assert "stdio_0" in result
        assert "http_1" in result

    def test_string_name_resolves_against_user_config(self, tmp_path: _Path) -> None:
        home = tmp_path / "home"
        _write_claude_json(home, {"atlassian": {"type": "http", "url": "https://x"}})
        result = _resolve_mcp_servers(
            ["atlassian"], role="r", teammate_id="t", home_dir=home,
        )
        # Verbatim dict from ~/.claude.json (no transformation per spec).
        assert result == {"atlassian": {"type": "http", "url": "https://x"}}

    def test_string_name_unresolvable_warns_and_skips(
        self, tmp_path: _Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        home = tmp_path / "home"
        _write_claude_json(home, {})  # empty config
        with caplog.at_level(_logging.WARNING, logger="claude_crew.sdk_teammate"):
            result = _resolve_mcp_servers(
                ["ghost-server"], role="explorer", teammate_id="t-xyz",
                home_dir=home,
            )
        # Skipped, not crashed.
        assert result == {}
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "t-xyz" in m and "explorer" in m and "ghost-server" in m
            for m in warn_msgs
        )

    def test_mixed_entries_preserve_order_and_shape(self, tmp_path: _Path) -> None:
        home = tmp_path / "home"
        _write_claude_json(home, {"atlassian": {"type": "http", "url": "x"}})
        result = _resolve_mcp_servers(
            ["atlassian", {"type": "stdio", "name": "local", "command": "uv"}],
            role="r", teammate_id="t", home_dir=home,
        )
        assert result == {
            "atlassian": {"type": "http", "url": "x"},
            "local": {"type": "stdio", "command": "uv"},
        }

    def test_tuple_entries_accepted(self, tmp_path: _Path) -> None:
        """PackFrontmatter stores mcpServers as a tuple; resolver must accept that."""
        home = tmp_path / "home"
        _write_claude_json(home, {"atlassian": {"type": "http"}})
        result = _resolve_mcp_servers(
            ("atlassian",), role="r", teammate_id="t", home_dir=home,
        )
        assert result == {"atlassian": {"type": "http"}}

    def test_home_dir_injection_isolates_test(self, tmp_path: _Path) -> None:
        """Sentinel D-11 / MF-3: explicit home_dir prevents reading the real ~/.claude.json."""
        # Plant a unique server name nowhere except in tmp_path.
        home = tmp_path / "isolated_home"
        unique_name = "f17-isolation-probe-zzz"
        _write_claude_json(home, {unique_name: {"type": "stdio"}})
        result = _resolve_mcp_servers(
            [unique_name], role="r", teammate_id="t", home_dir=home,
        )
        # If home_dir wasn't honored, this would be unresolvable and skipped.
        assert unique_name in result


class TestSdkTeammateMcpServersWiring:
    """Integration: pack mcpServers reaches ClaudeAgentOptions.mcp_servers via _run."""

    async def test_pack_inline_dict_reaches_options(self, broker, monkeypatch) -> None:
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test", prompt="be a thing",
            model="claude-haiku-4-5-20251001", tools=["Read"],
            mcpServers=[{"type": "stdio", "name": "local-x", "command": "uv"}],
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role, agents={"builder": agent_def},
            )

        tid = await broker.spawn_teammate(role="builder", name=None, factory=factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.mcp_servers == {"local-x": {"type": "stdio", "command": "uv"}}

    async def test_pack_string_name_resolves_via_patched_user_config(
        self, broker, monkeypatch,
    ) -> None:
        """SC-5 string-name path. Monkeypatch _load_user_mcp_servers since
        SdkTeammate._run calls the helper with home_dir=None (uses real ~)."""
        from claude_agent_sdk.types import AgentDefinition
        from claude_crew import sdk_teammate as sdk_mod

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)
        # Patch the helper called inside _resolve_mcp_servers to inject our config.
        monkeypatch.setattr(
            sdk_mod, "_load_user_mcp_servers",
            lambda home_dir=None: {"atlassian": {"type": "http", "url": "https://x"}},
        )

        agent_def = AgentDefinition(
            description="test", prompt="be a thing",
            model="claude-haiku-4-5-20251001", tools=["Read"],
            mcpServers=["atlassian"],
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role, agents={"builder": agent_def},
            )

        tid = await broker.spawn_teammate(role="builder", name=None, factory=factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        assert opts.mcp_servers == {"atlassian": {"type": "http", "url": "https://x"}}

    async def test_pack_no_mcp_servers_no_options_key(
        self, broker, monkeypatch,
    ) -> None:
        """Default behavior unchanged: no mcpServers in pack → mcp_servers stays at default."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test", prompt="be a thing",
            model="claude-haiku-4-5-20251001", tools=["Read"],
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role, agents={"builder": agent_def},
            )

        tid = await broker.spawn_teammate(role="builder", name=None, factory=factory)
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
        ))
        await _wait_for_lead_messages(broker, 1)
        opts = captured["options"]
        # ClaudeAgentOptions defaults mcp_servers to {} via field(default_factory=dict).
        assert opts.mcp_servers == {} or opts.mcp_servers is None


class TestSdkTeammateMemoryWarn:
    """SC-6: pack memory at teammate spawn → WARN, no options key."""

    async def test_memory_warns_and_no_options_key(
        self, broker, monkeypatch, caplog,
    ) -> None:
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        captured = _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test", prompt="be a thing",
            model="claude-haiku-4-5-20251001", tools=["Read"],
            memory="project",
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role, agents={"my-role": agent_def},
            )

        with caplog.at_level(_logging.WARNING, logger="claude_crew.sdk_teammate"):
            tid = await broker.spawn_teammate(role="my-role", name=None, factory=factory)
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
            ))
            await _wait_for_lead_messages(broker, 1)

        # ClaudeAgentOptions has no `memory` field — verify nothing leaked there.
        opts = captured["options"]
        assert not hasattr(opts, "memory") or getattr(opts, "memory", None) is None

        # WARN fired naming role, teammate_id, value.
        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "my-role" in m and "memory" in m and "project" in m
            for m in warn_msgs
        ), f"expected memory WARN naming role + value; got {warn_msgs}"

    async def test_no_memory_no_warn(self, broker, monkeypatch, caplog) -> None:
        """No memory in pack → no WARN (negative path scoped per Sentinel guidance)."""
        from claude_agent_sdk.types import AgentDefinition

        fake = FakeSDKClient(scripted_responses=[text_response("ok")])
        _patch_sdk(monkeypatch, fake)

        agent_def = AgentDefinition(
            description="test", prompt="be a thing",
            model="claude-haiku-4-5-20251001", tools=["Read"],
        )

        def factory(id, name, role, **_kwargs):
            return SdkTeammate(
                id=id, name=name, role=role, agents={"builder": agent_def},
            )

        with caplog.at_level(_logging.WARNING, logger="claude_crew.sdk_teammate"):
            tid = await broker.spawn_teammate(role="builder", name=None, factory=factory)
            await broker.send(Envelope(
                id=new_message_id(), seq=0,
                sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="hi",
            ))
            await _wait_for_lead_messages(broker, 1)

        warn_msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        # Scope: no warn that NAMES `memory` as a declared field.
        # (Liveness-related warnings unrelated to memory may legitimately fire.)
        assert not any(
            "pack declares memory" in m for m in warn_msgs
        ), f"expected no memory-pack-WARN; got {warn_msgs}"
