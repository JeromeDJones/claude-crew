"""Live SDK tests. Gated by CLAUDE_CREW_LIVE_TESTS=1.

These tests cost real money and require working Claude credentials.
SC-4 (UUID recall over 10+ turns) and the SC-5 spike (CLAUDE.md
loading via setting_sources) live here.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


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
