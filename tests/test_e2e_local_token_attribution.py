"""E2E integration tests: OpenAI-shape token attribution for local-backend teammates.

Exercises the full pipeline for local backend (ccr → llama.cpp) token/cost telemetry:
  AT#1: Fake-SDK E2E — OpenAI-shape ResultMessage usage attributed through the full
        broker → status_snapshot() → get_teammate_status() pipeline.
  AT#8: Live-gated probe — real local backend (CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1).
        Skips cleanly (not ERROR) when env gate is unset or servers unreachable.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from claude_crew import sdk_teammate as sdk_module
from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.sdk_teammate import SdkTeammate
from tests.fakes.sdk import FakeSDKClient, text_response_with_openai_usage

# ── helpers ──────────────────────────────────────────────────────────────────


def _patch_sdk(monkeypatch: pytest.MonkeyPatch, fake: FakeSDKClient) -> None:
    """Redirect ClaudeSDKClient construction to the given fake."""
    def _ctor(options: Any = None) -> FakeSDKClient:
        fake.options = options
        return fake

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _ctor)


def _local_sdk_factory(id: str, name: str, role: str, **kwargs: Any) -> SdkTeammate:
    """Factory that threads env through to SdkTeammate (required for is_local detection).

    The broker injects env into factory_kwargs when spawn_teammate is called with
    env=..., so this factory must accept and forward it to SdkTeammate.__init__.
    """
    return SdkTeammate(id=id, name=name, role=role, env=kwargs.get("env"))


async def _wait_lead(broker: Broker, count: int, timeout: float = 3.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}"
    )


def _send(broker: Broker, recipient: str, payload: Any) -> Any:
    return broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=recipient, timestamp=0.0,
        payload=payload,
    ))


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_shape_usage_attributed_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AT#1: OpenAI-shape usage on a local-backend teammate is attributed end-to-end.

    Given a fake ClaudeSDKClient returning ResultMessage with OpenAI-shape usage
    (prompt_tokens=1200, completion_tokens=350, cached_tokens=900, cost=0.0),
    when the broker spawns a teammate with ANTHROPIC_BASE_URL env and drives one turn,
    then get_teammate_status reflects the correct token counts and zero cost.

    Verifies the full pipeline:
      fake_sdk → _handle_one_turn (is_local=True) → _collect_response_text
        → _extract_token_cost_from_rm (OpenAI branch)
        → _end_turn accumulation → status_snapshot() → get_teammate_status()
    """
    fake = FakeSDKClient(
        scripted_responses=[
            text_response_with_openai_usage(
                "local response",
                prompt_tokens=1200,
                completion_tokens=350,
                cached_tokens=900,
                cumulative_cost_usd=0.0,
            ),
        ]
    )
    _patch_sdk(monkeypatch, fake)

    local_env = {
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
        "ANTHROPIC_API_KEY": "sk-local",
    }

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(
            role="local-probe",
            name=None,
            factory=_local_sdk_factory,
            env=local_env,
        )
        await _send(broker, tid, "hello")
        await _wait_lead(broker, 1)

        status = broker.get_teammate_status(tid)

        assert status["total_input_tokens"] == 1200, (
            f"expected total_input_tokens=1200; got {status['total_input_tokens']} — "
            "OpenAI prompt_tokens not flowing through _extract_token_cost_from_rm"
        )
        assert status["total_output_tokens"] == 350, (
            f"expected total_output_tokens=350; got {status['total_output_tokens']} — "
            "OpenAI completion_tokens not flowing through _extract_token_cost_from_rm"
        )
        assert status["last_turn_input_tokens"] == 1200, (
            f"expected last_turn_input_tokens=1200; got {status['last_turn_input_tokens']}"
        )
        assert status["last_turn_output_tokens"] == 350, (
            f"expected last_turn_output_tokens=350; got {status['last_turn_output_tokens']}"
        )
        assert status["total_cost_usd"] == 0.0, (
            f"expected total_cost_usd=0.0; got {status['total_cost_usd']}"
        )
    finally:
        await broker.shutdown_all()


@pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS") != "1",
    reason=(
        "live local backend gated; set CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1 "
        "with ccr and llama.cpp running to enable"
    ),
)
@pytest.mark.asyncio
async def test_live_local_backend_token_attribution() -> None:
    """AT#8: Live probe — real local backend produces non-zero token counts.

    Given CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1 and a local backend
    (ccr → llama.cpp) reachable, spawns a real SdkTeammate with local_backend
    env and asserts total_input_tokens > 0 and total_output_tokens > 0 after
    one turn. Confirms OpenAI-shape usage flows through the production pipeline.

    Skipped by default (gate-env unset) — does not run in CI.
    Skips (not errors) if local servers are unreachable at test time.

    Configure servers via env vars (defaults match ccr/llama.cpp defaults):
      CLAUDE_CREW_LOCAL_LLAMA_URL  (default: http://127.0.0.1:8080)
      CLAUDE_CREW_LOCAL_CCR_URL    (default: http://127.0.0.1:3456)
    """
    import urllib.error
    import urllib.request

    llama_url = os.environ.get("CLAUDE_CREW_LOCAL_LLAMA_URL", "http://127.0.0.1:8080")
    ccr_url = os.environ.get("CLAUDE_CREW_LOCAL_CCR_URL", "http://127.0.0.1:3456")

    # Probe llama.cpp reachability before spawning a real teammate.
    # A GET to /v1/chat/completions will likely return 405; that's fine — it means
    # the server is up. Only URLError (connection refused / timeout) means absent.
    try:
        urllib.request.urlopen(f"{llama_url}/v1/chat/completions", timeout=3)
    except urllib.error.HTTPError:
        # 4xx/5xx → server is up (it just rejected the GET), proceed.
        pass
    except urllib.error.URLError:
        pytest.skip(f"llama.cpp server unreachable at {llama_url} — start it before running AT#8")
    except OSError:
        pytest.skip(f"llama.cpp server unreachable at {llama_url} (OS error)")

    from claude_crew.factories import sdk_factory
    from claude_crew.local_backend import local_backend_env

    local_env = local_backend_env(base_url=ccr_url)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(
            role="live-local-probe",
            name=None,
            factory=sdk_factory,
            env=local_env,
        )

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="Hi. Reply with exactly one word: hello.",
        ))

        # Wait up to 120s for the local backend to respond.
        deadline = asyncio.get_event_loop().time() + 120.0
        while asyncio.get_event_loop().time() < deadline:
            if len(broker.get_messages(recipient=LEAD_ID)) >= 1:
                break
            await asyncio.sleep(0.5)

        msgs = broker.get_messages(recipient=LEAD_ID)
        if not msgs:
            pytest.skip(
                "no response from local backend within 120s — "
                "server may be too slow or ccr is not routing correctly"
            )

        status = broker.get_teammate_status(tid)
        assert status["total_input_tokens"] > 0, (
            f"expected total_input_tokens > 0; got {status['total_input_tokens']} — "
            "OpenAI-shape attribution may be broken (prompt_tokens not extracted)"
        )
        assert status["total_output_tokens"] > 0, (
            f"expected total_output_tokens > 0; got {status['total_output_tokens']} — "
            "OpenAI-shape attribution may be broken (completion_tokens not extracted)"
        )
    finally:
        await broker.shutdown_all()
