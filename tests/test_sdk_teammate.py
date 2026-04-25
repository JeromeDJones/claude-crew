"""Implementation-level tests for SdkTeammate.

The SDK's ClaudeSDKClient is monkey-patched with FakeSDKClient. Tests
exercise the SdkTeammate against a real Broker — same approach as
test_stub_teammate.py.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.sdk_teammate import (
    SHUTDOWN_TIMEOUT_SECONDS,
    SdkTeammate,
    _classify_error,
    _payload_to_prompt,
    RateLimitedError,
)
from tests.fakes.sdk import FakeSDKClient, text_response


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
    def _factory(id, name, role):
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
        assert fake.queries_received[0] == ("hello", "default")

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
        assert all(q[1] == "default" for q in fake.queries_received)

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

    async def test_response_hangs_produces_timeout_error(
        self, broker, monkeypatch,
    ) -> None:
        # Patch the timeout to a small value so the test runs fast.
        monkeypatch.setattr(sdk_module, "TURN_TIMEOUT_SECONDS", 0.1)

        fake = FakeSDKClient(
            scripted_responses=[
                [],  # turn 1 hangs (no response yielded)
                text_response("turn-2-ok"),  # turn 2 succeeds
            ],
            response_hangs=[True, False],
        )
        _patch_sdk(monkeypatch, fake)

        tid = await broker.spawn_teammate(
            role="r", name=None, factory=_factory_for(fake),
        )
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q1",
        ))
        await _wait_for_lead_messages(broker, 1, timeout=1.0)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[0].payload.get("error") == "invalid_response"
        assert "stuck" in msgs[0].payload.get("message", "").lower() \
            or "subprocess" in msgs[0].payload.get("message", "").lower()

        # Confirm the loop is still alive: send another message.
        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0, payload="q2",
        ))
        await _wait_for_lead_messages(broker, 2, timeout=1.0)
        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs[1].payload.get("text") == "turn-2-ok"

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

        assert fake.aenter_count == 1
        assert fake.aexit_count == 1
        # No teammate task should be alive.
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
        # Give the turn a moment to enter receive_response().
        await asyncio.sleep(0.05)

        start = asyncio.get_event_loop().time()
        await broker.kill_teammate(tid)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.5, f"shutdown took {elapsed:.2f}s"

        sdk_tasks = [
            t for t in asyncio.all_tasks()
            if t.get_name().startswith("sdk-")
        ]
        assert all(t.done() for t in sdk_tasks)
