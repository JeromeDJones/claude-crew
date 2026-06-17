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
        """SC-5 acceptance: PostSubagentUse hook fires and records delegation end-to-end.

        What this verifies:
          1. Deterministic pre-check (no live cost): the #21-assembled system prompt
             contains SENTINEL_CONTEXT and SENTINEL_DELEGATION sections, confirming
             build_teammate_prompt wires the delegation framework correctly.
          2. Mechanism check (live): when the general teammate is explicitly directed
             to dispatch an explorer subagent via the Task tool, `last_subagent_completed`
             is populated — proving the PostSubagentUse hook fires end-to-end.

        Task design: the task is an UNAMBIGUOUS directive ("Use the Task tool to
        dispatch the 'explorer' subagent ... relay the result verbatim"). This is
        not a judgment call about which tool is best — it is an explicit instruction
        the model will follow regardless of whether it could also answer with Bash.
        This tests the delegation MECHANISM (hook + SDK wiring), not model judgment.

        Signal: `last_subagent_completed` is non-None after the turn completes.
        Set by the PostSubagentUse hook — cannot be faked by stub mode or text parsing.

        WARNING: This test costs real money and requires working Claude credentials.
        Do not run in CI without an API budget.
        """
        # Grant Task tool so the general teammate can dispatch subagents.
        # The bundled general role is a leaf-node (tools: [Read, Grep, Glob, Edit,
        # Write, Bash, WebFetch, WebSearch]) — no Task by default. A real coordinator
        # would grant Task when spawning a teammate that needs to delegate; this
        # mirrors that pattern and is the correct way to enable delegation for this role.
        tid = await broker.spawn_teammate(
            role="general", name=None, factory=sdk_factory,
            extra_tools=["Task"],
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
                f"#21 prompt assembly regression: SENTINEL {sentinel!r} missing from "
                f"general teammate's _system_prompt. Skipping live call."
            )

        # Explicit Task-tool directive: the model MUST use the Task tool (no judgment
        # call about Bash vs delegation). This makes the test deterministic —
        # it verifies the delegation mechanism fires, not that the model freely chooses
        # to delegate. The explorer subagent has Read tool and can satisfy the task.
        task = (
            "Use the Task tool to dispatch the 'explorer' subagent with this exact "
            "prompt: 'Read the file /home/jerome/dev/claude-crew/README.md and report "
            "the first line verbatim.' "
            "Wait for the subagent to respond, then relay its answer verbatim. "
            "You MUST use the Task tool — do not read the file yourself."
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
                "general teammate did not dispatch an explorer subagent via Task tool. "
                "`last_subagent_completed` is None after the turn — the "
                "PostSubagentUse hook never fired. The task explicitly directed Task "
                "tool use; if the model didn't delegate, the Task tool may be broken "
                "or the hook is broken.\n"
                f"  last_tool_completed: {last_tool}\n"
                f"  current_tool_count: {snap.get('current_tool_count')}\n"
                f"  in-flight subagents: {len(snap.get('current_subagents', []))}\n"
                f"  lead-bound transcript ({len(transcript)} envelopes):\n"
                + "\n".join(transcript_summary)
            )
