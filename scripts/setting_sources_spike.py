"""Empirical spike: does setting_sources=[] actually suppress CLAUDE.md in SDK mode?

SC-5 from Feature #11 (Lightweight Subagent Context):
  Q1. When a teammate is spawned with setting_sources=[], does it NOT know the
      user's name (which lives in ~/.claude/CLAUDE.md via USER.md)?
  Q2. When spawned with setting_sources=["user","project"] (the default), does it
      KNOW the user's name? (control — confirms the source file is actually there)

Method:
  - Session A: setting_sources=[] — ask "What is the name of the user you work with?"
  - Session B: setting_sources=["user","project"] — same question
  - Session C: setting_sources=None (SDK picks default) — same question

Gate: CLAUDE_CREW_LIVE_TESTS=1 in environment.
Cost: ~$0.03 (haiku, three minimal turns).

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/setting_sources_spike.py`

Findings written to doc/research/setting-sources-sdk-behavior.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

_MODEL = "claude-haiku-4-5-20251001"
_QUESTION = (
    "Do you have an identity or persona name? If so, what is it? "
    "Also: are you aware of something called 'the six virtues'? "
    "Answer only from what you actually know — do not guess or invent."
)
_SYSTEM = (
    "You are a minimal probe session. Answer the question asked. Be brief."
)


def _gated() -> None:
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skip: set CLAUDE_CREW_LIVE_TESTS=1 to run", file=sys.stderr)
        sys.exit(0)


async def _ask(setting_sources: list[str] | None, disable_memory: bool = False) -> str:
    """Spawn one SDK session with the given setting_sources, return the text reply."""
    opts_kwargs: dict = {"model": _MODEL, "system_prompt": _SYSTEM}
    if setting_sources is not None:
        opts_kwargs["setting_sources"] = setting_sources
    if disable_memory:
        opts_kwargs["env"] = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

    options = ClaudeAgentOptions(**opts_kwargs)
    chunks: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(_QUESTION)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        chunks.append(block.text)
            if isinstance(msg, ResultMessage):
                break
    return "".join(chunks).strip()


def _classify(reply: str, sources_label: str) -> dict:
    # "Kael" and being able to list/describe the six virtues can only come from SOUL.md.
    # The word "virtue" alone is not sufficient — sessions without CLAUDE.md say
    # "I don't know the six virtues" which contains the word but not the knowledge.
    knows_identity = "kael" in reply.lower()
    # Positive signal: lists virtues by name OR says they're in SOUL.md
    knows_virtues = (
        "soul.md" in reply.lower()
        or ("authenticity" in reply.lower() and "courage" in reply.lower())
        or ("six virtues" in reply.lower() and "authenticity" in reply.lower())
    )
    loaded_claudemd = knows_identity or knows_virtues

    if "[]" in sources_label:
        if loaded_claudemd:
            finding = f"FAIL — CLAUDE.md still loaded with {sources_label} (knows identity={knows_identity}, virtues={knows_virtues})"
        else:
            finding = f"PASS — CLAUDE.md NOT loaded with {sources_label}"
    else:
        if loaded_claudemd:
            finding = f"PASS (control) — CLAUDE.md loaded with {sources_label} as expected"
        else:
            finding = f"INCONCLUSIVE (control) — CLAUDE.md NOT loaded with {sources_label} (expected it to be)"

    return {"sources": sources_label, "reply": reply, "loaded_claudemd": loaded_claudemd, "finding": finding}


async def main() -> int:
    _gated()

    print("Session A — setting_sources=[] …", flush=True)
    reply_a = await _ask([])
    print(f"  reply: {reply_a!r}")
    result_a = _classify(reply_a, "[]")
    print(f"  → {result_a['finding']}")

    print("Session B — setting_sources=[], disable_auto_memory=True …", flush=True)
    reply_b = await _ask([], disable_memory=True)
    print(f"  reply: {reply_b!r}")
    result_b = _classify(reply_b, "[] + no_memory")
    print(f"  → {result_b['finding']}")

    print("Session C — setting_sources=['user','project'] (explicit default, control) …", flush=True)
    reply_c = await _ask(["user", "project"])
    print(f"  reply: {reply_c!r}")
    result_c = _classify(reply_c, "['user', 'project']")
    print(f"  → {result_c['finding']}")

    findings_path = Path(__file__).parent.parent / "doc" / "research" / "setting-sources-sdk-behavior.md"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join([
        "# setting_sources SDK Behavior — Spike Findings",
        "",
        "Empirical results from `scripts/setting_sources_spike.py`.",
        "Re-run to refresh; do not edit by hand.",
        "",
        "## Summary",
        "",
        "| Session | setting_sources | Knows CLAUDE.md? | Finding |",
        "|---------|----------------|-----------------|---------|",
        f"| A | `[]` | {result_a['loaded_claudemd']} | {result_a['finding']} |",
        f"| B | `[] + no_memory` | {result_b['loaded_claudemd']} | {result_b['finding']} |",
        f"| C | `['user','project']` | {result_c['loaded_claudemd']} | {result_c['finding']} |",
        "",
        "## Replies",
        "",
        f"**Session A** (`[]`): {result_a['reply']}",
        "",
        f"**Session B** (`[] + no_memory`): {result_b['reply']}",
        "",
        f"**Session C** (`['user','project']`): {result_c['reply']}",
    ])
    findings_path.write_text(content)
    print(f"\nFindings written to {findings_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
