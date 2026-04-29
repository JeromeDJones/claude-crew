"""Empirical spike: MCP cold-start reliability in SDK mode.

The original mcp_spike.py confirmed global MCP loads in SDK mode — but only
on a warm run. The first run gave TOOL_MISSING, the second succeeded. We
attributed it to Atlassian OAuth token refresh, but "probably OAuth" isn't
the same as "confirmed."

This spike answers:

  Q1. Is the cold-start failure Atlassian-specific (HTTP MCP + OAuth),
      or does it affect all MCP types including stdio?

  Q2. On repeated cold starts (fresh client per run, no warm-up), how
      consistent is MCP availability? Run the same probe 3 times in
      sequence with independent ClaudeSDKClient instances.

  Q3. Does a 5-second warm-up delay before the first tool call close
      the cold-start gap?

  Q4. Does claude-crew's own stdio MCP server load reliably in a
      fresh SDK session? (This is the one that matters for production —
      teammates need to reach their own crew tools.)

Method:
  - Scenario A: Atlassian probe (HTTP MCP) × 3 independent sessions.
    If run 1 fails and run 2+ succeed → OAuth warm-up confirmed.
    If all fail or all pass → different cause.

  - Scenario B: claude-crew stdio MCP probe × 3 independent sessions.
    Tests whether stdio MCP (no external auth) has the same cold-start gap.
    Probe tool: mcp__claude_crew__list_crew (should always return []).

  - Scenario C: Atlassian probe with 5s delay before first tool call.
    If this passes where Scenario A run 1 fails → delay closes the gap.

Gate: CLAUDE_CREW_LIVE_TESTS=1 in environment.
Cost: ~$0.15–0.25 (haiku, 7 sessions).

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/mcp_cold_start_spike.py`

Findings written to doc/research/mcp-cold-start-behavior.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

_MODEL = "claude-haiku-4-5-20251001"
_ATLASSIAN_PROBE = "mcp__atlassian__atlassianUserInfo"
_CREW_PROBE = "mcp__claude_crew__list_crew"

_PROBE_SYSTEM = (
    "You are a minimal probe session. Follow instructions exactly. "
    "When asked to call a tool, call it. If the tool does not exist or is "
    "unavailable, reply with exactly: TOOL_MISSING: <tool_name>. "
    "Do not invent results."
)


def _gated() -> None:
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skip: set CLAUDE_CREW_LIVE_TESTS=1 to run", file=sys.stderr)
        sys.exit(0)


async def _drain(client: ClaudeSDKClient) -> tuple[str, list[str]]:
    text_chunks: list[str] = []
    tools_called: list[str] = []
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tools_called.append(block.name)
        if isinstance(msg, ResultMessage):
            break
    return "".join(text_chunks), tools_called


async def _probe_once(tool_name: str, delay_s: float = 0.0) -> dict:
    """One fresh SDK session, optionally delayed before the tool call."""
    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt=_PROBE_SYSTEM,
        setting_sources=["user"],
    )
    async with ClaudeSDKClient(options=options) as client:
        if delay_s > 0:
            await asyncio.sleep(delay_s)
        await client.query(
            f"Call the tool '{tool_name}' and return its raw output. "
            f"If unavailable, say exactly: TOOL_MISSING: {tool_name}"
        )
        text, tools = await _drain(client)

    called = tool_name in tools
    missing = "TOOL_MISSING" in text and tool_name.split("__")[-1] in text
    if called:
        status = "PASS"
    elif missing:
        status = "FAIL — TOOL_MISSING"
    else:
        status = f"INCONCLUSIVE — text: {text[:120]!r}"
    return {"status": status, "tools_called": tools, "text": text[:300]}


# ---------------------------------------------------------------------------
# Scenario A — Atlassian × 3 cold starts
# ---------------------------------------------------------------------------

async def scenario_a_atlassian_cold_starts() -> dict:
    results = []
    for i in range(1, 4):
        print(f"    run {i}/3 …", end=" ", flush=True)
        r = await _probe_once(_ATLASSIAN_PROBE)
        print(r["status"])
        results.append(r)

    statuses = [r["status"] for r in results]
    if all(s == "PASS" for s in statuses):
        finding = "CONSISTENT PASS — Atlassian MCP loads on all 3 cold starts"
    elif statuses[0].startswith("FAIL") and all(s == "PASS" for s in statuses[1:]):
        finding = "COLD-START GAP CONFIRMED — run 1 failed, runs 2+3 passed (OAuth warm-up)"
    elif all(s.startswith("FAIL") for s in statuses):
        finding = "CONSISTENT FAIL — Atlassian MCP unavailable in SDK mode"
    else:
        finding = f"INCONSISTENT — {statuses}"

    return {"scenario": "A", "question": "Atlassian HTTP MCP × 3 cold starts",
            "results": results, "finding": finding}


# ---------------------------------------------------------------------------
# Scenario B — claude-crew stdio MCP × 3 cold starts
# ---------------------------------------------------------------------------

async def scenario_b_crew_cold_starts() -> dict:
    results = []
    for i in range(1, 4):
        print(f"    run {i}/3 …", end=" ", flush=True)
        r = await _probe_once(_CREW_PROBE)
        print(r["status"])
        results.append(r)

    statuses = [r["status"] for r in results]
    if all(s == "PASS" for s in statuses):
        finding = "CONSISTENT PASS — claude-crew stdio MCP loads reliably on cold start"
    elif statuses[0].startswith("FAIL") and all(s == "PASS" for s in statuses[1:]):
        finding = "COLD-START GAP — run 1 failed, runs 2+3 passed (stdio has same warm-up issue)"
    elif all(s.startswith("FAIL") for s in statuses):
        finding = "CONSISTENT FAIL — claude-crew stdio MCP unavailable in SDK mode"
    else:
        finding = f"INCONSISTENT — {statuses}"

    return {"scenario": "B", "question": "claude-crew stdio MCP × 3 cold starts",
            "results": results, "finding": finding}


# ---------------------------------------------------------------------------
# Scenario C — Atlassian with 5s warm-up delay
# ---------------------------------------------------------------------------

async def scenario_c_delayed_warmup() -> dict:
    print("    probing with 5s delay …", end=" ", flush=True)
    r = await _probe_once(_ATLASSIAN_PROBE, delay_s=5.0)
    print(r["status"])

    if r["status"] == "PASS":
        finding = "DELAY CLOSES GAP — 5s warm-up before first call succeeds"
    else:
        finding = f"DELAY DOES NOT HELP — {r['status']}"

    return {"scenario": "C", "question": "Atlassian with 5s warm-up delay",
            "results": [r], "finding": finding}


# ---------------------------------------------------------------------------
# Findings writer
# ---------------------------------------------------------------------------

def _fmt_scenario(s: dict) -> str:
    lines = [
        f"### Scenario {s['scenario']}: {s['question']}",
        "",
        f"**Finding:** {s['finding']}",
        "",
        "**Per-run results:**",
        "",
    ]
    for i, r in enumerate(s["results"], 1):
        lines.append(f"- Run {i}: `{r['status']}` — tools called: `{r['tools_called']}`")
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    _gated()

    print("Scenario A — Atlassian HTTP MCP × 3 cold starts …")
    a = await scenario_a_atlassian_cold_starts()
    print(f"  → {a['finding']}")

    print("Scenario B — claude-crew stdio MCP × 3 cold starts …")
    b = await scenario_b_crew_cold_starts()
    print(f"  → {b['finding']}")

    print("Scenario C — Atlassian with 5s warm-up delay …")
    c = await scenario_c_delayed_warmup()
    print(f"  → {c['finding']}")

    findings_path = (
        Path(__file__).parent.parent / "doc" / "research" / "mcp-cold-start-behavior.md"
    )
    content = "\n".join([
        "# MCP Cold-Start Behavior — Spike Findings",
        "",
        "Empirical results from `scripts/mcp_cold_start_spike.py`.",
        "Re-run to refresh; do not edit by hand.",
        "",
        "## Summary",
        "",
        "| Scenario | Finding |",
        "|---|---------|",
        f"| A — Atlassian × 3 cold starts | {a['finding']} |",
        f"| B — claude-crew stdio × 3 cold starts | {b['finding']} |",
        f"| C — Atlassian + 5s warm-up delay | {c['finding']} |",
        "",
        "## Detail",
        "",
        _fmt_scenario(a),
        _fmt_scenario(b),
        _fmt_scenario(c),
    ])
    findings_path.write_text(content)
    print(f"\nFindings written to {findings_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
