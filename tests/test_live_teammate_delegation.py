"""Live behavioral test for Feature #21 SC-5 — delegation actually happens.

Gated by CLAUDE_CREW_LIVE_TESTS=1. Skipped in CI by default.

What this verifies that static tests can't:
  - Given the #21-assembled system prompt, a real general-purpose teammate
    ACTUALLY delegates file-read work to the explorer subagent rather than
    reading files itself.
  - `last_subagent_completed` is populated after the turn, proving the
    SDK's PostSubagentUse hook fired and the delegation was real.

What this does NOT prove:
  - That delegation happens for every task or every model. This is one
    behavioral observation, not a contract. A sufficiently large or
    complex task might still cause the model to inline file reads before
    delegating. The test is constructed to be small and deterministic
    enough that the delegation prompt reliably wins.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory
from claude_crew.teammate_prompt import (
    SENTINEL_CONTEXT,
    SENTINEL_DELEGATION,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


DELEGATION_TIMEOUT = 90.0  # bounded cost per co-architect Q2 tightening
# Note: co-architect's "max_turns=5" tightening is not implemented — there is no
# spawn-time knob for max_turns on top-level teammates today (only the per-turn
# backstop_seconds). The 90s deadline + the per-turn backstop together provide
# sufficient bounding for the test's cost envelope.


async def _wait_for_lead(broker: Broker, count: int, timeout: float = DELEGATION_TIMEOUT) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead messages within {timeout}s; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}",
    )


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


class TestGeneralPurposeTeammateDelegation:
    """SC-5 live behavioral probe — #21 prompt enables real delegation."""

    async def test_live_general_purpose_teammate_delegates_to_explorer(
        self, broker: Broker,
    ) -> None:
        """SC-5 acceptance: a real general-purpose teammate delegates to the explorer
        subagent when asked to search files, rather than reading files itself.

        Signal: `last_subagent_completed` is non-None after the turn completes.
        This field is set by the PostSubagentUse hook in SdkTeammate only when
        a subagent invocation actually runs and returns — it cannot be faked by
        stub mode or by the model simply mentioning subagents in its reply text.

        Why `last_subagent_completed` is the right signal here:
          - It is set by the SDK hook, not by text parsing — no false positives.
          - It survives turn completion (it is NOT cleared between turns, only on
            teammate death). A subagent that completes in turn N will still show
            up in a snapshot taken after turn N.
          - The alternative (`current_subagents` non-empty mid-run) would require
            polling during the run and is racy. Post-completion is deterministic.

        WARNING: This test costs real money and requires working Claude credentials.
        Do not run in CI without an API budget.
        """
        tid = await broker.spawn_teammate(
            role="general-purpose", name=None, factory=sdk_factory,
        )

        # Deterministic pre-check: the assembled prompt must contain all
        # SENTINEL_* headings. If a future #21-related regression breaks the
        # prompt assembly, this catches it before paying for a live API call.
        teammate_pre = broker._teammates.get(tid)
        assert teammate_pre is not None
        sys_prompt = getattr(teammate_pre, "_system_prompt", "") or ""
        # SENTINEL_SUBAGENTS dropped 2026-05-17 — its section duplicated the
        # framework-injected Agent tool description.
        for sentinel in (SENTINEL_CONTEXT, SENTINEL_DELEGATION):
            assert sentinel in sys_prompt, (
                f"#21 prompt assembly regression: SENTINEL_{sentinel!r} missing from "
                f"general-purpose teammate's _system_prompt. Skipping live call."
            )

        task = (
            "Find every place in /home/jerome/dev/claude-crew/claude_crew/ that calls "
            "`parse_pack_text`. Report each as file:line. "
            "Use the explorer subagent for the search; do not read files yourself."
        )

        await broker.send(Envelope(
            id=new_message_id(), seq=0,
            sender=LEAD_ID, recipient=tid, timestamp=0.0,
            payload=task,
        ))

        await _wait_for_lead(broker, 1, timeout=DELEGATION_TIMEOUT)

        # Read the teammate instance directly (same pattern as test_live_sdk.py).
        teammate = broker._teammates.get(tid)
        assert teammate is not None, "teammate no longer in _teammates after turn"

        snap = teammate.status_snapshot()
        last_subagent = snap.get("last_subagent_completed")

        if last_subagent is None:
            # Diagnostic dump on failure: dump the lead-bound transcript so we
            # can see WHY delegation didn't happen (model read directly,
            # never tried, error mid-turn). Per co-architect's Q2 tightening:
            # failure should tell us why, not just that.
            transcript = broker.get_messages(recipient=LEAD_ID)
            transcript_summary = [
                f"  [{i}] from={env.sender} payload={str(env.payload)[:200]!r}"
                for i, env in enumerate(transcript)
            ]
            last_tool = snap.get("last_tool_completed")
            raise AssertionError(
                "general-purpose teammate did not delegate to any subagent. "
                "`last_subagent_completed` is None after the turn — the "
                "PostSubagentUse hook never fired. The #21 delegation prompt "
                "may not be effective for this task/model combination, or the "
                "hook is broken.\n"
                f"  last_tool_completed: {last_tool}\n"
                f"  current_tool_count: {snap.get('current_tool_count')}\n"
                f"  in-flight subagents: {len(snap.get('current_subagents', []))}\n"
                f"  lead-bound transcript ({len(transcript)} envelopes):\n"
                + "\n".join(transcript_summary)
            )
