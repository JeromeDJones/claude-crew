"""E2E integration tests for Feature #14: Token/Cost Telemetry pipeline.

Exercises the full pipeline: broker spawn → multi-turn drives → UIServer
dashboard payload. Four tests:
  1. Happy path: two teammates, multi-turn, kill one, assert totals preserved.
  2. Sad path (malformed): turn 2 emits malformed usage; turns 1 and 3 valid.
  3. Sad path (mid-turn kill): kill mid-turn preserves last cumulative.
  4. Live probe (gated): real SDK ResultMessage carries Anthropic-standard keys.
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
from claude_crew.ui_server import UIServer
from tests.fakes.sdk import FakeSDKClient, text_response_with_usage


# ── helpers ──────────────────────────────────────────────────────────────────


def _patch_sdk(monkeypatch: pytest.MonkeyPatch, fake: FakeSDKClient) -> None:
    """Redirect ClaudeSDKClient construction to the given fake."""
    def _ctor(options: Any = None) -> FakeSDKClient:
        fake.options = options
        return fake

    monkeypatch.setattr(sdk_module, "ClaudeSDKClient", _ctor)


def _sdk_factory(id: str, name: str, role: str, **_kwargs: Any) -> SdkTeammate:
    return SdkTeammate(id=id, name=name, role=role)


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


def _send(broker: Broker, recipient: str, payload: Any) -> "asyncio.coroutine":
    return broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=recipient, timestamp=0.0,
        payload=payload,
    ))


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_token_cost_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full token/cost pipeline E2E: two teammates, multi-turn, kill one.

    BDD (Phase 3, T5, happy path):
      - Teammate A: 2 turns, cumulative cost $0.10 → $0.40
      - Teammate B: 3 turns, cumulative cost $0.25 → $0.50 → $0.75
      - Instance cost == $1.15 (A=$0.40 + B=$0.75)
      - Both agents in agents[] with correct individual costs
      - Kill A → instance cost still $1.15 (tombstone preserves $0.40)
      - agents[] now contains only B
    """
    broker = Broker()
    try:
        # ── Teammate A: 2 turns ──────────────────────────────────────────────
        fake_a = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "a-turn1",
                    turn_input_tokens=100,
                    turn_output_tokens=50,
                    cumulative_cost_usd=0.10,
                ),
                text_response_with_usage(
                    "a-turn2",
                    turn_input_tokens=300,  # per-turn: 400 total = 100 + 300
                    turn_output_tokens=150,  # per-turn: 200 total = 50 + 150
                    cumulative_cost_usd=0.40,
                ),
            ]
        )
        _patch_sdk(monkeypatch, fake_a)
        tid_a = await broker.spawn_teammate(role="builder", name="A", factory=_sdk_factory)

        for i in range(2):
            await _send(broker, tid_a, f"a-q{i}")
        await _wait_lead(broker, 2)

        # ── Teammate B: 3 turns ──────────────────────────────────────────────
        fake_b = FakeSDKClient(
            scripted_responses=[
                text_response_with_usage(
                    "b-turn1",
                    turn_input_tokens=250,
                    turn_output_tokens=125,
                    cumulative_cost_usd=0.25,
                ),
                text_response_with_usage(
                    "b-turn2",
                    turn_input_tokens=250,  # per-turn: 500 total = 250 + 250
                    turn_output_tokens=125,  # per-turn: 250 total = 125 + 125
                    cumulative_cost_usd=0.50,
                ),
                text_response_with_usage(
                    "b-turn3",
                    turn_input_tokens=250,  # per-turn: 750 total = 500 + 250
                    turn_output_tokens=125,  # per-turn: 375 total = 250 + 125
                    cumulative_cost_usd=0.75,
                ),
            ]
        )
        _patch_sdk(monkeypatch, fake_b)
        tid_b = await broker.spawn_teammate(role="reviewer", name="B", factory=_sdk_factory)

        for i in range(3):
            await _send(broker, tid_b, f"b-q{i}")
        await _wait_lead(broker, 5)  # 2 from A + 3 from B

        # ── Assert pre-kill state ────────────────────────────────────────────
        ui = UIServer(broker, port=0)
        instance, _ = ui._build_local_instance(broker.snapshot(log_limit=200))

        assert abs(instance["cost"] - 1.15) < 1e-9, (
            f"expected instance cost=1.15 (A=0.40 + B=0.75), got {instance['cost']}"
        )
        assert len(instance["agents"]) == 2, (
            f"expected 2 alive agents, got {len(instance['agents'])}"
        )

        agent_by_id = {a["id"]: a for a in instance["agents"]}
        assert abs(agent_by_id[tid_a]["cost"] - 0.40) < 1e-9, (
            f"A cost: expected 0.40 (cumulative), got {agent_by_id[tid_a]['cost']}"
        )
        # Token counts: A = 100+300=400, B = 250+250+250=750
        assert agent_by_id[tid_a]["tokens"]["in"] == 400, (
            f"A input tokens: expected 400 (100+300), got {agent_by_id[tid_a]['tokens'].get('in')}"
        )
        assert agent_by_id[tid_b]["cost"] == 0.75, (
            f"B cost: expected 0.75 (cumulative), got {agent_by_id[tid_b]['cost']}"
        )
        assert agent_by_id[tid_b]["tokens"]["in"] == 750, (
            f"B input tokens: expected 750 (250+250+250), got {agent_by_id[tid_b]['tokens'].get('in')}"
        )

        # ── Kill A, re-build state ───────────────────────────────────────────
        await broker.kill_teammate(tid_a)

        instance2, _ = ui._build_local_instance(broker.snapshot(log_limit=200))

        # SC-3: tombstone preserves A's $0.40 in the instance aggregate.
        assert abs(instance2["cost"] - 1.15) < 1e-9, (
            f"after killing A, instance cost should still be 1.15; "
            f"got {instance2['cost']}"
        )

        # D-10: agents[] is alive-only — A excluded, B present.
        agent_ids2 = [a["id"] for a in instance2["agents"]]
        assert tid_a not in agent_ids2, "dead teammate A must not appear in agents[]"
        assert tid_b in agent_ids2, "alive teammate B must appear in agents[]"
        assert len(instance2["agents"]) == 1, (
            f"expected 1 agent after kill, got {len(instance2['agents'])}"
        )

    finally:
        await broker.shutdown_all()


@pytest.mark.asyncio
async def test_e2e_malformed_midstream_does_not_corrupt_totals(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sad path — malformed usage in turn 2 does not corrupt cumulative totals.

    BDD (Phase 3, T5):
      - Turn 1: valid, cost $0.10, tokens 100/50
      - Turn 2: ResultMessage.usage = "not-a-dict" (malformed); WARNING logged
      - Turn 3: valid, cost $0.50, tokens 500/250
      After all three turns:
        snap["total_cost_usd"] == 0.50 (turn-3 cumulative)
        snap["total_input_tokens"] == 500 (turn-3 cumulative)
        WARNING logged for turn 2's malformed payload

    Per D-8 (per-field independence): malformed usage invalidates token
    extraction only. total_cost_usd on the same ResultMessage (0.99 in the
    malformed turn) DID update on that turn. Turn 3 then overwrites with
    its own cumulative values. The final state reflects turn 3.
    """
    import logging
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

    # Turn 2: malformed usage (a string, not a dict).
    malformed_rm = ResultMessage(
        subtype="success",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=2,
        session_id="fake",
        total_cost_usd=0.20,  # cost field itself is valid on this ResultMessage
        usage="not-a-dict",  # type: ignore[arg-type]
    )
    malformed_turn = [
        AssistantMessage(content=[TextBlock(text="turn2")], model="fake-model"),
        malformed_rm,
    ]

    fake = FakeSDKClient(
        scripted_responses=[
            text_response_with_usage(
                "turn1",
                turn_input_tokens=100,
                turn_output_tokens=50,
                cumulative_cost_usd=0.10,
            ),
            malformed_turn,
            text_response_with_usage(
                "turn3",
                turn_input_tokens=400,  # per-turn: 500 total = 100 + 400
                turn_output_tokens=200,  # per-turn: 250 total = 50 + 200
                cumulative_cost_usd=0.50,
            ),
        ]
    )
    _patch_sdk(monkeypatch, fake)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        for i in range(3):
            await _send(broker, tid, f"q{i}")
        await _wait_lead(broker, 3)

        snap = broker._teammates[tid].status_snapshot()

        # Cost: Turn 3's cumulative value (overwrite semantics, D-2).
        assert snap["total_cost_usd"] == 0.50, (
            f"expected turn-3 cumulative 0.50; got {snap['total_cost_usd']}"
        )
        # Tokens: accumulate per-turn values (turn-2 malformed, so tokens unchanged from turn-1).
        # Turn 1: 100/50, Turn 2: malformed (skipped), Turn 3: 400/200 (per-turn)
        # Total: 100+400=500, 50+200=250
        assert snap["total_input_tokens"] == 500, (
            f"expected accumulated 500 (100+400); got {snap['total_input_tokens']}"
        )
        assert snap["total_output_tokens"] == 250, (
            f"expected accumulated 250 (50+200); got {snap['total_output_tokens']}"
        )
        # Last-turn (per-turn delta, overwrite semantics) reflects turn 3's
        # values — the most recent successful turn. Turn 2 was malformed
        # (no usage extracted) so it doesn't overwrite the last-turn fields.
        assert snap["last_turn_input_tokens"] == 400, (
            f"expected last-turn input 400 (turn 3); got {snap['last_turn_input_tokens']}"
        )
        assert snap["last_turn_output_tokens"] == 200, (
            f"expected last-turn output 200 (turn 3); got {snap['last_turn_output_tokens']}"
        )

        # WARNING must have been logged for the malformed turn-2 usage.
        warning_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "not a dict" in r.message
        ]
        assert warning_records, (
            "Expected WARNING log about malformed usage (not a dict); "
            f"got records: {[r.message for r in caplog.records if r.levelno == logging.WARNING]}"
        )

        # E2E: dashboard also reflects the correct totals.
        ui = UIServer(broker, port=0)
        instance, _ = ui._build_local_instance(broker.snapshot(log_limit=200))
        assert abs(instance["cost"] - 0.50) < 1e-9, (
            f"dashboard instance cost should be 0.50; got {instance['cost']}"
        )

    finally:
        await broker.shutdown_all()


@pytest.mark.asyncio
async def test_e2e_kill_mid_turn_preserves_last_cumulative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sad path — killing a teammate preserves the last successfully completed turn's totals.

    BDD (Phase 3, T5 SC-4):
      - Turn 1 completes with cumulative cost $0.10.
      - Turn 2 is started but its ResultMessage never arrives (response_hangs=True).
      - While turn 2 is in flight, the teammate is killed.
      - TeammateInfo.total_cost_usd_at_death == 0.10 (last successfully observed cumulative).
      - The in-flight turn's contribution is not recovered — acceptable per SC-4.

    This exercises the mid-turn-kill path: tombstone captures status_snapshot()
    before any ResultMessage from turn 2 arrives, so the at-death values reflect
    turn 1's cumulative totals.
    """
    fake = FakeSDKClient(
        scripted_responses=[
            text_response_with_usage(
                "turn1",
                turn_input_tokens=100,
                turn_output_tokens=50,
                cumulative_cost_usd=0.10,
            ),
            # turn 2 scripted but will never deliver (response_hangs=True)
            text_response_with_usage(
                "turn2",
                turn_input_tokens=200,  # per-turn; would be 300 total if completed
                turn_output_tokens=100,
                cumulative_cost_usd=0.30,
            ),
        ],
        response_hangs=[False, True],
    )
    _patch_sdk(monkeypatch, fake)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)

        # Complete turn 1.
        await _send(broker, tid, "q1")
        await _wait_lead(broker, 1)

        # Verify turn-1 baseline is captured.
        snap_turn1 = broker._teammates[tid].status_snapshot()
        assert snap_turn1["total_cost_usd"] == 0.10

        # Start turn 2 — will hang; no ResultMessage will arrive.
        await _send(broker, tid, "q2")
        # Brief yield to let the teammate enter the hung receive_response() loop.
        await asyncio.sleep(0.05)

        # Kill mid-turn — tombstone captures status_snapshot() before turn 2 completes.
        await broker.kill_teammate(tid)

        info = broker._info[tid]
        assert info.alive is False, "teammate should be tombstoned after kill"

        # at_death fields reflect turn-1's last cumulative (turn-2 never finished).
        assert info.total_cost_usd_at_death == 0.10, (
            f"expected at_death cost 0.10 (turn-1 cumulative); "
            f"got {info.total_cost_usd_at_death}"
        )
        assert info.total_input_tokens_at_death == 100, (
            f"expected at_death input_tokens 100; got {info.total_input_tokens_at_death}"
        )
        assert info.total_output_tokens_at_death == 50, (
            f"expected at_death output_tokens 50; got {info.total_output_tokens_at_death}"
        )
        # Last-turn at_death preserves turn-1's per-turn values (the only completed turn).
        assert info.last_turn_input_tokens_at_death == 100, (
            f"expected at_death last_turn_input 100; got {info.last_turn_input_tokens_at_death}"
        )
        assert info.last_turn_output_tokens_at_death == 50, (
            f"expected at_death last_turn_output 50; got {info.last_turn_output_tokens_at_death}"
        )

        # get_teammate_status on the dead branch also returns the correct at-death values.
        status = broker.get_teammate_status(tid)
        assert status["alive"] is False
        assert status["total_cost_usd"] == 0.10
        assert status["total_input_tokens"] == 100
        assert status["total_output_tokens"] == 50

    finally:
        await broker.shutdown_all()


@pytest.mark.asyncio
async def test_peak_invocation_input_tracks_single_largest_llm_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug fix 2026-05-17: ResultMessage.usage.input_tokens is the SUM across
    every LLM invocation in the turn (the API billing total). For a tool-using
    turn it can be many times the model's context window. The context-window
    bar should NOT use that number — it should use the peak single-invocation
    input, which is what each AssistantMessage.usage.input_tokens carries.

    This test scripts a turn with three AssistantMessages whose individual
    inputs are 80k / 195k / 130k (simulating a 3-invocation turn). The final
    ResultMessage reports the billing total: 405k. The teammate snapshot must
    expose:
      - last_turn_input_tokens = 405,000 (billing — unchanged)
      - last_turn_peak_invocation_input_tokens = 195,000 (cliff signal — NEW)

    Without this fix, the dashboard would show "405k/200k" which is nonsense
    (a single invocation never exceeded the context window).
    """
    fake = FakeSDKClient(
        scripted_responses=[
            text_response_with_usage(
                "done",
                turn_input_tokens=405_000,  # billing total (sum across invocations)
                turn_output_tokens=1_000,
                cumulative_cost_usd=2.50,
                invocation_inputs=[80_000, 195_000, 130_000],  # per-invocation
            ),
        ]
    )
    _patch_sdk(monkeypatch, fake)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        await _send(broker, tid, "do a tool-heavy thing")
        await _wait_lead(broker, 1)

        snap = broker._teammates[tid].status_snapshot()

        # Billing total — unchanged semantics, still the API-summed input.
        assert snap["last_turn_input_tokens"] == 405_000, (
            f"expected last_turn_input_tokens=405000 (API billing total); "
            f"got {snap['last_turn_input_tokens']}"
        )

        # Peak per-invocation — the cliff signal the dashboard bar should use.
        assert snap["last_turn_peak_invocation_input_tokens"] == 195_000, (
            f"expected last_turn_peak_invocation_input_tokens=195000 "
            f"(max of 80k/195k/130k single-invocation inputs); got "
            f"{snap['last_turn_peak_invocation_input_tokens']}"
        )

        # Sanity: peak < billing total — confirms the new field captures the
        # right thing and isn't accidentally aliased to the billing sum.
        assert (
            snap["last_turn_peak_invocation_input_tokens"]
            < snap["last_turn_input_tokens"]
        ), "peak must be strictly less than billing sum for multi-invocation turns"
    finally:
        await broker.shutdown_all()


@pytest.mark.asyncio
async def test_active_model_exposed_on_snapshot_after_assistant_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active-model-display: after a turn produces an AssistantMessage, the
    teammate's status_snapshot() exposes ``active_model`` carrying the
    AssistantMessage.model verbatim.

    This is the API-authoritative model id (echoed back in the Messages API
    response). The dashboard relies on this for the "what model is actually
    running" chip and the model-chain renderer.
    """
    fake = FakeSDKClient(
        scripted_responses=[
            text_response_with_usage(
                "pong",
                turn_input_tokens=100,
                turn_output_tokens=10,
                cumulative_cost_usd=0.001,
            ),
        ]
    )
    _patch_sdk(monkeypatch, fake)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        await _send(broker, tid, "ping")
        await _wait_lead(broker, 1)

        snap = broker._teammates[tid].status_snapshot()
        # fake-model is the model the FakeSDKClient stamps on AssistantMessages
        # — this is the value the real SDK would put there from the API
        # response payload (message_parser.py:148-150).
        assert snap["active_model"] == "fake-model", (
            f"expected active_model='fake-model'; got {snap['active_model']!r}"
        )

        # Broker snapshot also surfaces it on the live entry.
        live = [e for e in broker.snapshot().live if e.info.id == tid]
        assert len(live) == 1
        # status copy (D-2 deepcopy) preserves the active_model key.
        assert live[0].status.get("active_model") == "fake-model"
    finally:
        await broker.shutdown_all()


@pytest.mark.asyncio
async def test_active_model_none_before_any_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active-model-display: a freshly spawned teammate that hasn't yet
    produced an AssistantMessage exposes ``active_model=None`` so the UI
    can fall back to the configured value with the "not observed yet"
    affordance.
    """
    fake = FakeSDKClient(scripted_responses=[])
    _patch_sdk(monkeypatch, fake)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        snap = broker._teammates[tid].status_snapshot()
        assert snap["active_model"] is None
    finally:
        await broker.shutdown_all()


@pytest.mark.asyncio
async def test_active_model_preserved_at_tombstone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """active-model-display: when a teammate is killed after producing an
    AssistantMessage, the broker's tombstone preserves active_model so the
    dashboard's dead-row chip still shows the model the API actually used.
    """
    fake = FakeSDKClient(
        scripted_responses=[
            text_response_with_usage(
                "done",
                turn_input_tokens=200,
                turn_output_tokens=20,
                cumulative_cost_usd=0.002,
            ),
        ]
    )
    _patch_sdk(monkeypatch, fake)

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="r", name=None, factory=_sdk_factory)
        await _send(broker, tid, "do work")
        await _wait_lead(broker, 1)

        await broker.kill_teammate(tid)

        info = broker._info[tid]
        assert info.active_model_at_death == "fake-model"

        # Broker snapshot dead-build path exposes it on the wire-shape dict.
        dead = broker.snapshot().teammates
        dead_match = [d for d in dead if d.id == tid]
        assert len(dead_match) == 1
        # The dict-shape build path runs through Broker.snapshot internals —
        # exercise the to-dict accessor via the public API:
        dict_snap = broker.get_teammate_status(tid)
        assert dict_snap is not None
        assert dict_snap["active_model"] == "fake-model"
    finally:
        await broker.shutdown_all()


@pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)
@pytest.mark.asyncio
async def test_live_sdk_result_message_uses_standard_usage_keys() -> None:
    """Live probe: real SDK ResultMessage carries Anthropic-standard usage keys.

    Verifies Assumption A-1: ResultMessage.usage contains 'input_tokens' and
    'output_tokens' (standard Anthropic names). If this fails, the extraction
    logic in _collect_response_text would silently return None for all token
    fields and the dashboard would always show zero tokens.

    The test uses the simpler proxy assertion: if _total_input_tokens > 0
    AND _total_cost_usd > 0 on the SdkTeammate after one real turn, then
    the SDK-provided values must have been extracted correctly. If the usage
    keys were absent or wrong, both fields would stay at zero (graceful
    degradation per SC-6), and this assertion would fail — alerting us to
    a key-name change in the Anthropic SDK.

    WARNING: This test costs real money and requires working Claude credentials.
    Do not run in CI without an API budget.
    """
    from claude_crew.factories import sdk_factory

    broker = Broker()
    try:
        tid = await broker.spawn_teammate(role="live-probe", name=None, factory=sdk_factory)

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload="Hi. Reply with exactly one word: hello.",
        ))

        # Wait up to 90s for the live SDK to respond.
        deadline = asyncio.get_event_loop().time() + 90.0
        while asyncio.get_event_loop().time() < deadline:
            if len(broker.get_messages(recipient=LEAD_ID)) >= 1:
                break
            await asyncio.sleep(0.5)

        msgs = broker.get_messages(recipient=LEAD_ID)
        assert msgs, "no response received from live SDK within 90s"

        # Read directly from the live SdkTeammate instance.
        teammate = broker._teammates.get(tid)
        assert teammate is not None, "teammate no longer in _teammates after turn"

        input_tokens = teammate._total_input_tokens
        cost_usd = teammate._total_cost_usd

        assert input_tokens > 0, (
            f"_total_input_tokens == 0 after a real turn — usage keys may have changed. "
            f"Check ResultMessage.usage key names in the installed claude_agent_sdk. "
            f"Expected 'input_tokens' and 'output_tokens' (Anthropic standard)."
        )
        assert cost_usd > 0, (
            f"_total_cost_usd == 0.0 after a real turn — ResultMessage.total_cost_usd "
            f"may be absent or None. Check the SDK version."
        )

    finally:
        await broker.shutdown_all()
