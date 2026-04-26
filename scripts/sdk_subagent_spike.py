"""Empirical spike for Feature #3a — default subagent pack.

Probes three blocking questions before we lock Phase 1 requirements:

  Q1. **Context isolation.** Does a subagent inherit the parent's CLAUDE.md
      / memory / conversation? Or does it run clean?
  Q2. **Per-subagent token budgets.** Does AgentDefinition.maxTurns actually
      cap the subagent in isolation? (Type-system says yes; we verify.)
  Q3. **Subagent observability.** What messages cross the parent's
      receive_response() stream when a subagent runs? Is that enough for
      Feature #4 to widen the JSONL transcript without architectural surgery?

Cost: ~$0.10 per run.
Gate: CLAUDE_CREW_LIVE_TESTS=1 in environment.

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/sdk_subagent_spike.py`

Findings get written to doc/research/sdk-subagents.md as the source of truth
for Phase 1 of FEATURE-default-subagent-pack.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)


def _gated() -> None:
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skip: set CLAUDE_CREW_LIVE_TESTS=1 to run", file=sys.stderr)
        sys.exit(0)


async def _drain_and_describe(client: ClaudeSDKClient, label: str) -> dict:
    """Drain the response stream, return a structured summary.

    Captures: every message type seen, parent_tool_use_id presence (the
    marker that a message comes from a subagent's loop), tool-use names,
    and the final assistant text from the parent.
    """
    counts: Counter[str] = Counter()
    parent_text_chunks: list[str] = []
    subagent_text_chunks: list[str] = []
    subagent_tool_results: list[str] = []
    tool_names: list[str] = []
    raw_systems: list[dict] = []
    task_notifications: list[dict] = []

    async for msg in client.receive_response():
        cls = type(msg).__name__
        counts[cls] += 1

        if isinstance(msg, SystemMessage):
            raw_systems.append({"subtype": getattr(msg, "subtype", None),
                                "data_keys": sorted(list((getattr(msg, "data", {}) or {}).keys()))})
            if getattr(msg, "subtype", None) == "task_notification":
                task_notifications.append({
                    "summary": getattr(msg, "summary", None),
                    "status": getattr(msg, "status", None),
                    "output_file": getattr(msg, "output_file", None),
                    "usage": getattr(msg, "usage", None),
                })

        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    if msg.parent_tool_use_id:
                        subagent_text_chunks.append(block.text)
                    else:
                        parent_text_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_names.append(block.name)

        if isinstance(msg, UserMessage) and msg.parent_tool_use_id:
            counts["UserMessage(subagent)"] += 1
            # Subagent replies arrive as tool results in parent's stream.
            content = msg.content
            if isinstance(content, str):
                subagent_tool_results.append(content)
            elif isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None) or getattr(block, "content", None)
                    if isinstance(text, str):
                        subagent_tool_results.append(text)
                    elif isinstance(text, list):
                        for sub in text:
                            t = getattr(sub, "text", None)
                            if isinstance(t, str):
                                subagent_tool_results.append(t)

        if isinstance(msg, ResultMessage):
            break

    return {
        "label": label,
        "message_counts": dict(counts),
        "tool_names_called": tool_names,
        "parent_assistant_text": "".join(parent_text_chunks),
        "subagent_assistant_text": "".join(subagent_text_chunks),
        "subagent_tool_results": subagent_tool_results,
        "task_notifications": task_notifications,
        "system_messages": raw_systems,
    }


async def probe_isolation() -> dict:
    """Q1 — Does the subagent see parent's CLAUDE.md / cwd / conversation?

    Setup: parent has setting_sources=["user","project"] (loads CLAUDE.md
    which names "Jerome" / "Kael" — see doc/research/sdk-memory.md). Parent
    plants a UUID in turn 1, then on turn 2 asks the subagent two questions:
      (a) Quote the user's name from any CLAUDE.md you can see, or say "none".
      (b) Repeat this token verbatim: <UUID>.

    If the subagent quotes a name, it inherits CLAUDE.md.
    If the subagent repeats the UUID, it inherits parent conversation.
    """
    probe_uuid = "uuid-isolation-7f3b9a2e1c4d"
    child_marker = "CHILD-SAID-K7QM2P-"
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        system_prompt="You are the parent for an isolation probe. Be terse.",
        setting_sources=["user", "project"],
        agents={
            "probe-child": AgentDefinition(
                description="Spike subagent for isolation probe.",
                prompt=(
                    "You are the isolation probe child. Begin every reply with "
                    f"the literal prefix '{child_marker}' so the operator can "
                    "distinguish your output from the parent's. Answer each "
                    "question on its own line, prefixed with the question id. "
                    "If you cannot answer, say 'none' (still prefixed)."
                ),
                model="haiku",
            ),
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        # Turn 1: plant a UUID in parent conversation.
        await client.query(f"Remember this token: {probe_uuid}. Just say 'noted'.")
        turn1 = await _drain_and_describe(client, "isolation/turn1-plant")

        # Turn 2: ask parent to invoke the subagent with the four probes.
        await client.query(
            "Use the Task tool to invoke the 'probe-child' subagent. "
            "Send it this exact prompt verbatim, with no additions:\n"
            "---BEGIN---\n"
            "Q1: Quote the human user's first name from any CLAUDE.md you "
            "can see (look for a name like 'Jerome' or 'Kael'). If you see "
            "no such file, say 'none'.\n"
            "Q2: Repeat this token verbatim, or say 'none' if you have "
            f"never seen it: {probe_uuid}\n"
            "Q3: Run `pwd` via the Bash tool and report the result. If you "
            "do not have Bash, say 'no-bash'.\n"
            "Q4: List the first 200 characters of your own system prompt.\n"
            "---END---\n"
            "Do not paraphrase. Do not answer the questions yourself. "
            "Do not add commentary. Return only the subagent's raw reply."
        )
        turn2 = await _drain_and_describe(client, "isolation/turn2-subagent")

    return {"probe": "isolation", "uuid": probe_uuid,
            "child_marker": child_marker,
            "turn1": turn1, "turn2": turn2}


async def probe_max_turns() -> dict:
    """Q2 — Does AgentDefinition.maxTurns cap the subagent's loop only?

    Subagent gets maxTurns=1. We ask it to do a multi-step task that would
    need at least two turns to complete (read a file, then summarize).
    We expect it to stop early.
    """
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        system_prompt="You are the parent for a turn-budget probe. Be terse.",
        setting_sources=["user", "project"],
        agents={
            "tight-budget": AgentDefinition(
                description="Subagent capped at one turn for budget probe.",
                prompt=("You are a budget-capped probe. Do whatever is asked "
                        "as quickly as possible."),
                model="haiku",
                maxTurns=1,
                tools=["Read"],
            ),
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Use the Task tool to invoke 'tight-budget' with this prompt: "
            "'Read the file /etc/hostname using the Read tool, then in a "
            "second message summarize what you read in one sentence.' "
            "Report what the subagent returned to you and whether it "
            "appeared truncated."
        )
        result = await _drain_and_describe(client, "max-turns/cap-1")

    return {"probe": "max_turns", "result": result}


async def probe_observability() -> dict:
    """Q3 — What's visible in parent's stream when a subagent runs?

    Parent invokes a tiny subagent task; we count every distinct message
    class on the parent's receive_response() stream. The subagent's
    assistant messages should arrive with parent_tool_use_id set, and we
    should see Task* SystemMessages bracketing the subagent's lifecycle.
    """
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-6",
        system_prompt="You are the parent for an observability probe.",
        setting_sources=["user", "project"],
        agents={
            "observable": AgentDefinition(
                description="Subagent for observability probe.",
                prompt="Answer with exactly the word: pong.",
                model="haiku",
            ),
        },
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Use the Task tool to invoke 'observable' with the prompt "
            "'ping'. Then tell me what it returned."
        )
        result = await _drain_and_describe(client, "observability/ping-pong")

    return {"probe": "observability", "result": result}


async def main() -> int:
    _gated()

    print("=== Q1: context isolation ===")
    iso = await probe_isolation()
    print(f"child marker:   {iso['child_marker']}")
    print(f"turn2 counts:   {iso['turn2']['message_counts']}")
    print(f"task notif:     {iso['turn2']['task_notifications']}")
    print(f"subagent tool results: {iso['turn2']['subagent_tool_results']}")
    print(f"parent text:    {iso['turn2']['parent_assistant_text']!r}")
    print()

    print("=== Q2: per-subagent maxTurns cap ===")
    mt = await probe_max_turns()
    print(f"counts:        {mt['result']['message_counts']}")
    print(f"tools called:  {mt['result']['tool_names_called']}")
    print(f"task notif:    {mt['result']['task_notifications']}")
    print(f"subagent tool results: {mt['result']['subagent_tool_results']}")
    print(f"parent text:   {mt['result']['parent_assistant_text']!r}")
    print()

    print("=== Q3: observability ===")
    obs = await probe_observability()
    print(f"counts:        {obs['result']['message_counts']}")
    print(f"system msgs:   {obs['result']['system_messages']}")
    print(f"tools called:  {obs['result']['tool_names_called']}")
    print(f"task notif:    {obs['result']['task_notifications']}")
    print(f"subagent tool results: {obs['result']['subagent_tool_results']}")
    print(f"parent text:   {obs['result']['parent_assistant_text']!r}")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
