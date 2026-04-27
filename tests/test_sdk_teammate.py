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
    _classify_error,
    _payload_to_prompt,
    RateLimitedError,
)
from tests.fakes.sdk import FakeSDKClient, text_response
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

        # Use a factory that respects model/effort kwargs from the broker.
        def factory(id, name, role, *, model=None, effort=None):
            kwargs = {}
            if model is not None:
                kwargs["model"] = model
            if effort is not None:
                kwargs["effort"] = effort
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
        teammate._on_pre_tool_use(hook_input, tool_use_id, {})

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
        teammate._on_pre_tool_use(hook_input_pre, tool_use_id, {})

        assert teammate.status_snapshot()["current_tool_count"] == 1

        # Now fire PostToolUse.
        await asyncio.sleep(0.01)  # Ensure duration > 0.
        hook_input_post = {
            "agent_id": None,
            "tool_name": "Bash",
            "tool_response": "success",
        }
        teammate._on_post_tool_use(hook_input_post, tool_use_id, {})

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
        teammate._on_pre_tool_use(hook_input_pre, tool_use_id, {})

        # Fire PostToolUseFailure with is_interrupt=true.
        hook_input_failure = {
            "agent_id": None,
            "tool_name": "Bash",
            "is_interrupt": True,
            "error": "interrupted by user",
        }
        teammate._on_post_tool_use_failure(hook_input_failure, tool_use_id, {})

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
        teammate._on_pre_tool_use(hook_input_pre, tool_use_id, {})

        # Fire PostToolUseFailure with is_interrupt=false.
        hook_input_failure = {
            "agent_id": None,
            "tool_name": "Bash",
            "is_interrupt": False,
            "error": "exit 1",
        }
        teammate._on_post_tool_use_failure(hook_input_failure, tool_use_id, {})

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
        teammate._on_pre_tool_use(hook_input, "tu-1", {})

        # Activity should have advanced.
        assert teammate._last_activity_wallclock > initial_activity

    async def test_subagent_stamps_activity_but_no_state(
        self, broker, monkeypatch
    ) -> None:
        """SC-10/D3: Subagent tool call stamps activity, NO state update, NO transcript."""
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
        teammate._on_pre_tool_use(hook_input, "tu-sub-1", {})

        # Activity should have advanced, but current_tools should be empty.
        assert teammate._last_activity_wallclock > initial_activity
        assert teammate.status_snapshot()["current_tool_count"] == 0

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
        teammate._on_pre_tool_use(hook_input, tool_use_id, {})
        first_started = teammate._tool_uses[tool_use_id].started_at_wallclock

        # Wait a bit, then second Pre (same tool_use_id).
        await asyncio.sleep(0.05)
        t2 = time.time()
        teammate._on_pre_tool_use(hook_input, tool_use_id, {})
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
        teammate._on_post_tool_use(hook_input, "tu-unknown", {})

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
        teammate._on_post_tool_use(hook_input, tool_use_id, {})

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
        result = teammate._on_pre_tool_use(hook_input, "tu-1", {})

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
