"""Unit tests for local-model token attribution — extract-helper-dual-shape slice.

Covers AT#2, AT#3, AT#5, AT#6 from the local-model-attribution spec.

AT#2 — No double-counting of cached prompt tokens.
AT#3 — Peak-invocation input attributed from AssistantMessage OpenAI usage.
AT#5 — Cost flows through verbatim from ResultMessage.total_cost_usd.
AT#6 — Ambiguous dual-shape usage (both key families) prefers Anthropic keys.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

import pytest

from claude_crew.sdk_teammate import _collect_response_text
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from tests.fakes.sdk import text_response_with_openai_usage


# ── minimal fake client ───────────────────────────────────────────────────────


class _StreamClient:
    """Minimal async iterable used to drive _collect_response_text in isolation."""

    def __init__(self, messages: list[Any]) -> None:
        self._messages = messages

    async def receive_response(self) -> AsyncIterator[Any]:
        for msg in self._messages:
            yield msg


# ── AT#2: no double-counting of cached prompt tokens ─────────────────────────


@pytest.mark.asyncio
async def test_no_double_count_cached_prompt_tokens() -> None:
    """AT#2: prompt_tokens already includes cached; do not add cached_tokens again.

    Two turns:
      Turn 1: prompt_tokens=1000, cached_tokens=800
      Turn 2: prompt_tokens=1500, cached_tokens=1300
    Expected totals:
      total_input = 1000 + 1500 = 2500 (NOT 2500 + 800 + 1300 = 4600)
      last_turn_input after turn 2 = 1500
    """
    turn1_msgs = text_response_with_openai_usage(
        "turn one",
        prompt_tokens=1000,
        completion_tokens=100,
        cached_tokens=800,
    )
    turn2_msgs = text_response_with_openai_usage(
        "turn two",
        prompt_tokens=1500,
        completion_tokens=150,
        cached_tokens=1300,
    )

    result1 = await _collect_response_text(_StreamClient(turn1_msgs))
    result2 = await _collect_response_text(_StreamClient(turn2_msgs))

    # Each turn returns the non-double-counted value.
    assert result1.turn_input_tokens == 1000, (
        f"Turn 1: expected 1000, got {result1.turn_input_tokens}"
    )
    assert result2.turn_input_tokens == 1500, (
        f"Turn 2: expected 1500, got {result2.turn_input_tokens}"
    )

    # Accumulation (what SdkTeammate would do) = 2500, not 4600.
    total = (result1.turn_input_tokens or 0) + (result2.turn_input_tokens or 0)
    assert total == 2500, f"Expected total 2500, got {total}"

    # Output tokens are not double-counted either.
    assert result1.turn_output_tokens == 100
    assert result2.turn_output_tokens == 150


# ── AT#3: peak-invocation input from AssistantMessage OpenAI usage ────────────


@pytest.mark.asyncio
async def test_peak_invocation_input_openai_shape() -> None:
    """AT#3: peak_invocation_input_tokens tracks max AssistantMessage.usage.prompt_tokens.

    Two AssistantMessages: usage.prompt_tokens=800 then usage.prompt_tokens=1500.
    Peak must be 1500.
    """
    messages = text_response_with_openai_usage(
        "result",
        prompt_tokens=1500,       # ResultMessage billing total
        completion_tokens=200,
        invocation_inputs=[800, 1500],  # per-AssistantMessage openai-shape usage
    )

    result = await _collect_response_text(_StreamClient(messages))

    assert result.peak_invocation_input_tokens == 1500, (
        f"Expected peak 1500, got {result.peak_invocation_input_tokens}"
    )


# ── AT#5: cost flows through verbatim ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cost_flows_through_verbatim() -> None:
    """AT#5: total_cost_usd is not zeroed for non-zero local-backend values.

    A local ResultMessage with total_cost_usd=0.0042 (non-zero hypothetical)
    must arrive unchanged — the helper must not clear it just because the
    backend is local or the shape is OpenAI.
    """
    messages = text_response_with_openai_usage(
        "response",
        prompt_tokens=100,
        completion_tokens=50,
        cumulative_cost_usd=0.0042,
    )

    result = await _collect_response_text(_StreamClient(messages))

    assert result.cumulative_cost_usd == pytest.approx(0.0042), (
        f"Expected 0.0042, got {result.cumulative_cost_usd}"
    )
    # Tokens attributed correctly too.
    assert result.turn_input_tokens == 100
    assert result.turn_output_tokens == 50


# ── AT#6: ambiguous dual-shape prefers Anthropic keys ─────────────────────────


@pytest.mark.asyncio
async def test_dual_shape_prefers_anthropic_keys(caplog: pytest.LogCaptureFixture) -> None:
    """AT#6: when both input_tokens and prompt_tokens present, Anthropic wins.

    usage = {
        "input_tokens": 100, "output_tokens": 50,
        "prompt_tokens": 9999, "completion_tokens": 9999,
    }
    Expected: (per_turn_input, per_turn_output) == (100, 50).
    An INFO record must be logged mentioning both key families.
    """
    ambiguous_usage = {
        "input_tokens": 100,
        "output_tokens": 50,
        "prompt_tokens": 9999,
        "completion_tokens": 9999,
    }
    messages: list[Any] = [
        AssistantMessage(content=[TextBlock(text="hello")], model="fake-model"),
        ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="fake",
            total_cost_usd=0.01,
            usage=ambiguous_usage,
        ),
    ]

    with caplog.at_level(logging.INFO, logger="claude_crew.sdk_teammate"):
        result = await _collect_response_text(_StreamClient(messages))

    # Anthropic shape wins.
    assert result.turn_input_tokens == 100, (
        f"Expected 100 (Anthropic), got {result.turn_input_tokens}"
    )
    assert result.turn_output_tokens == 50, (
        f"Expected 50 (Anthropic), got {result.turn_output_tokens}"
    )

    # An INFO log must mention both key families.
    info_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "input_tokens" in r.getMessage()
        and "prompt_tokens" in r.getMessage()
    ]
    assert info_records, (
        "Expected an INFO log mentioning both 'input_tokens' and 'prompt_tokens'; "
        f"got records: {[r.getMessage() for r in caplog.records]}"
    )
