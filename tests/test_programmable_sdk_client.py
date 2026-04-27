"""Unit tests for ProgrammableSDKClient.

Five BDD scenarios matching Phase 3 T2 acceptance criteria.
Pure unit tests — no broker, no teammate, no SDK plumbing.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.fakes.programmable_sdk_client import ProgrammableSDKClient
from tests.fakes.sdk import text_response


# ---------------------------------------------------------------------------
# Scenario 1: events arrive at configured delays
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yields_events_at_configured_delays() -> None:
    """event_timings=[0.0, 0.5, 0.0] → second event ≥0.5s after first,
    third event ≤0.1s after second."""
    # Two SDK messages (AssistantMessage + ResultMessage = 2 events)
    # We need at least 3 yield points; use text_response twice to get 4 events.
    # Simpler: build a 3-event scripted list manually.
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    three_events = [
        AssistantMessage(content=[TextBlock(text="one")], model="fake"),
        AssistantMessage(content=[TextBlock(text="two")], model="fake"),
        ResultMessage(
            subtype="success", duration_ms=0, duration_api_ms=0,
            is_error=False, num_turns=1, session_id="default",
        ),
    ]

    client = ProgrammableSDKClient(
        scripted_responses=[three_events],
        event_timings=[0.0, 0.5, 0.0],
    )
    async with client:
        await client.query("go")

        timestamps: list[float] = []
        async for _ in client.receive_response():
            timestamps.append(time.monotonic())

    assert len(timestamps) == 3, f"expected 3 events, got {len(timestamps)}"

    gap_0_to_1 = timestamps[1] - timestamps[0]
    gap_1_to_2 = timestamps[2] - timestamps[1]

    assert gap_0_to_1 >= 0.5, (
        f"expected ≥0.5s gap between events 0 and 1, got {gap_0_to_1:.3f}s"
    )
    assert gap_1_to_2 < 0.1, (
        f"expected <0.1s gap between events 1 and 2, got {gap_1_to_2:.3f}s"
    )


# ---------------------------------------------------------------------------
# Scenario 2: interrupt records calls in normal mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_records_calls_in_normal_mode() -> None:
    """interrupt_behavior='normal' → returns immediately, records each call."""
    client = ProgrammableSDKClient(
        scripted_responses=[text_response("hi")],
        interrupt_behavior="normal",
    )

    before = time.monotonic()
    await client.interrupt()
    await client.interrupt()
    after = time.monotonic()

    assert len(client.interrupt_calls) == 2
    for ts in client.interrupt_calls:
        assert before <= ts <= after, f"call timestamp {ts} outside [{before}, {after}]"


# ---------------------------------------------------------------------------
# Scenario 3: interrupt hangs in hang mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_hangs_in_hang_mode() -> None:
    """interrupt_behavior='hang' → asyncio.TimeoutError within wait_for timeout;
    the call is still recorded before the hang begins."""
    client = ProgrammableSDKClient(interrupt_behavior="hang")

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client.interrupt(), timeout=0.5)

    # The call must be recorded *before* the hang starts.
    assert len(client.interrupt_calls) == 1, (
        f"expected interrupt_calls length 1, got {len(client.interrupt_calls)}"
    )


# ---------------------------------------------------------------------------
# Scenario 4: interrupt raises in raise mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_raises_in_raise_mode() -> None:
    """interrupt_behavior='raise' → raises configured exception type."""
    client = ProgrammableSDKClient(
        interrupt_behavior="raise",
        interrupt_raises=ConnectionError,
    )

    with pytest.raises(ConnectionError):
        await client.interrupt()

    # Call is recorded even when interrupt raises.
    assert len(client.interrupt_calls) == 1


# ---------------------------------------------------------------------------
# Scenario 5: settable returncode is observable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settable_returncode_is_observable() -> None:
    """transport_returncode=None; mutate to 137 mid-test; read it back."""
    client = ProgrammableSDKClient(transport_returncode=None)

    assert client._transport._process.returncode is None

    client._transport._process.returncode = 137

    assert client._transport._process.returncode == 137
