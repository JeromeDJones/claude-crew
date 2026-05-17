"""Empirical spike: can we disable project auto-memory injection for SDK teammates?

Question:
  Claude Code auto-injects ``~/.claude/projects/<sanitized-cwd>/memory/MEMORY.md``
  into every spawned subprocess. Can we turn this OFF for claude-crew teammates
  while leaving it ON for the operator's lead session?

Two candidate mechanisms surfaced from research (claude-code-guide agent):
  1. ``CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`` env var (documented at CLI level).
     Unknown whether SDK subprocesses honor it.
  2. ``setting_sources=[]`` on ClaudeAgentOptions. Known to suppress CLAUDE.md
     loading; unknown whether it also covers auto-memory (research said no,
     they're separate channels).

Method:
  Four sessions, asking the same question — "do you have a memory entry
  containing the phrase 'coordinator-in-the-loop is the moat'?" That string
  appears in the project MEMORY.md index. If a session sees it, auto-memory
  is loaded for that session.

  - A: defaults (setting_sources=['user','project'], no env override) — control
  - B: defaults + CLAUDE_CODE_DISABLE_AUTO_MEMORY=1
  - C: setting_sources=[]
  - D: setting_sources=[] + CLAUDE_CODE_DISABLE_AUTO_MEMORY=1

Gate: CLAUDE_CREW_LIVE_TESTS=1 in environment.
Cost: ~$0.04 (haiku, four minimal turns).

Run: ``CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/auto_memory_disable_spike.py``

Findings written to ``doc/research/auto-memory-disable-sdk-behavior.md``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

_MODEL = "claude-haiku-4-5-20251001"
_SENTINEL = "coordinator-in-the-loop is the moat"
_QUESTION = (
    f"Do you have any memory entry in your loaded context containing the phrase "
    f"'{_SENTINEL}'? Answer Yes or No only. If Yes, quote the one bullet "
    f"verbatim. If No, say plainly: 'not in context'. Do NOT use any tool. "
    f"Only report what is already in your loaded context."
)
_SYSTEM = "You are a probe session. Answer concisely. No tool use."


def _gated() -> None:
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skip: set CLAUDE_CREW_LIVE_TESTS=1 to run", file=sys.stderr)
        sys.exit(0)


async def _ask(setting_sources: list[str] | None, disable_memory: bool) -> str:
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


def _classify(reply: str) -> tuple[bool, str]:
    """Return (memory_visible, verdict). memory_visible=True if sentinel quoted."""
    low = reply.lower()
    has_sentinel = _SENTINEL.lower() in low
    yes_word = low.lstrip().startswith("yes")
    if has_sentinel or yes_word:
        return True, "memory LOADED"
    return False, "memory NOT loaded"


async def main() -> int:
    _gated()

    configs = [
        ("A — defaults (control)",             ["user", "project"], False),
        ("B — defaults + DISABLE_AUTO_MEMORY", ["user", "project"], True),
        ("C — setting_sources=[]",             [],                  False),
        ("D — setting_sources=[] + DISABLE",   [],                  True),
    ]

    rows: list[tuple[str, str, bool, str]] = []
    for label, ss, dm in configs:
        print(f"{label} …", flush=True)
        reply = await _ask(ss, dm)
        visible, verdict = _classify(reply)
        print(f"  reply: {reply!r}")
        print(f"  → {verdict}")
        rows.append((label, reply, visible, verdict))

    findings_path = Path(__file__).parent.parent / "doc" / "research" / "auto-memory-disable-sdk-behavior.md"
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    md = [
        "# Auto-Memory Disable — SDK Spike Findings",
        "",
        f"Empirical results from `scripts/auto_memory_disable_spike.py`.",
        f"Sentinel phrase: `{_SENTINEL}`.",
        "Re-run to refresh; do not edit by hand.",
        "",
        "## Summary",
        "",
        "| Config | setting_sources | DISABLE_AUTO_MEMORY | Memory loaded? | Verdict |",
        "|---|---|---|---|---|",
    ]
    for label, _reply, visible, verdict in rows:
        ss_part = label.split("—", 1)[1].strip()
        md.append(f"| {label.split('—')[0].strip()} | {ss_part} | — | {visible} | {verdict} |")
    md.extend(["", "## Replies", ""])
    for label, reply, _visible, _verdict in rows:
        md.append(f"**{label}**:")
        md.append("")
        md.append(f"> {reply}")
        md.append("")
    findings_path.write_text("\n".join(md))
    print(f"\nFindings written to {findings_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
