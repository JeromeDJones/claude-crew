"""Empirical spike: MCP server behavior in SDK mode.

Probes three blocking questions before locking the claude-crew MCP forwarding
feature design:

  Q2. Do globally-configured MCP servers load in SDK mode?
      (CLAUDE_CODE_ENTRYPOINT=sdk-py subprocess + setting_sources=["user"])
      Tested by: Scenario A — no mcp_servers override, ask for Atlassian tool.

  Q1. Does ClaudeAgentOptions.mcp_servers merge with or replace global config?
      When non-empty, the SDK passes --mcp-config to the CLI subprocess.
      Does the CLI merge that with the global config or use it exclusively?
      Tested by: Scenario B — explicit mcp_servers with ONLY atlassian (not
      claude-crew). If mcp__claude_crew__* tools still appear, it's MERGE.
      If only atlassian tools appear, it's REPLACE.

  Q3a. Does ClaudeAgentOptions.tools block MCP tools from connected servers?
       Tested by: Scenario C — top-level session with tools=["Read","Grep"].
       If Atlassian tools still callable, allowlist doesn't block MCP.

  Q3b. Does AgentDefinition.tools block subagent MCP access?
       Tested by: Scenario D — parent spawns subagent with tools=["Read","Grep"].
       If subagent can call Atlassian tool, AgentDefinition.tools doesn't block MCP.

Globally-configured MCP servers used as probe targets (both in ~/.claude.json):
  atlassian  — HTTP: https://mcp.atlassian.com/v1/mcp
  claude-crew — stdio: uv --directory /home/jerome/dev/claude-crew run claude-crew

Cost: ~$0.10–0.20 per run (haiku sessions).
Gate: CLAUDE_CREW_LIVE_TESTS=1 in environment.

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/mcp_spike.py`

Findings written to doc/research/mcp-sdk-behavior.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    McpHttpServerConfig,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

_ATLASSIAN_URL = "https://mcp.atlassian.com/v1/mcp"
_MODEL = "claude-haiku-4-5-20251001"

_ATLASSIAN_PROBE_TOOL = "mcp__atlassian__atlassianUserInfo"
_CREW_PROBE_TOOL = "mcp__claude_crew__list_crew"

_PROBE_SYSTEM = (
    "You are a minimal probe session. Follow instructions exactly. "
    "When asked to call a tool, call it. If the tool does not exist or is "
    "unavailable, reply with the exact string 'TOOL_MISSING: <tool_name>' "
    "(substituting the tool name). Do not invent results."
)


def _gated() -> None:
    if os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1":
        print("skip: set CLAUDE_CREW_LIVE_TESTS=1 to run", file=sys.stderr)
        sys.exit(0)


async def _drain(client: ClaudeSDKClient) -> tuple[str, list[str]]:
    """Drain receive_response(); return (assistant_text, [tool_names_called])."""
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


# ---------------------------------------------------------------------------
# Scenario A — Q2: do global MCP servers load in SDK mode?
# ---------------------------------------------------------------------------

async def probe_a_global_mcp_loads() -> dict:
    """No explicit mcp_servers. Ask model to call the Atlassian probe tool.

    - Model calls the tool → Q2 = YES, global MCP loads in SDK mode.
    - Model says TOOL_MISSING → Q2 = NO, global MCP does not load.
    """
    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt=_PROBE_SYSTEM,
        setting_sources=["user"],
        # mcp_servers intentionally not set (default empty dict)
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Call the tool '{_ATLASSIAN_PROBE_TOOL}' and return its raw output. "
            f"If the tool is unavailable, say exactly: TOOL_MISSING: {_ATLASSIAN_PROBE_TOOL}"
        )
        text, tools = await _drain(client)

    return {
        "scenario": "A",
        "question": "Q2 — global MCP loads in SDK mode?",
        "tools_called": tools,
        "text": text,
        "finding": (
            "YES — global MCP loads"
            if _ATLASSIAN_PROBE_TOOL in tools
            else "NO — global MCP does NOT load (TOOL_MISSING)"
        ),
    }


# ---------------------------------------------------------------------------
# Scenario B — Q1: merge or replace?
# ---------------------------------------------------------------------------

async def probe_b_merge_vs_replace() -> dict:
    """Explicit mcp_servers with ONLY atlassian. Ask about claude-crew tools.

    Both atlassian and claude-crew are globally configured. If we pass only
    atlassian explicitly and claude-crew tools still appear → MERGE.
    If only atlassian tools appear → REPLACE.

    We ask the model to:
      1. Call the Atlassian probe tool (control — should work in both cases).
      2. Attempt the claude-crew probe tool (the discriminator).
    """
    atlassian_cfg: McpHttpServerConfig = {"type": "http", "url": _ATLASSIAN_URL}
    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt=_PROBE_SYSTEM,
        setting_sources=["user"],
        mcp_servers={"atlassian": atlassian_cfg},
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Do two things in order:\n"
            f"1. Call '{_ATLASSIAN_PROBE_TOOL}' and confirm it worked.\n"
            f"2. Call '{_CREW_PROBE_TOOL}' and return its output. "
            f"   If unavailable, say exactly: TOOL_MISSING: {_CREW_PROBE_TOOL}\n"
            f"Report both results."
        )
        text, tools = await _drain(client)

    atlassian_ok = _ATLASSIAN_PROBE_TOOL in tools
    crew_ok = _CREW_PROBE_TOOL in tools

    if crew_ok:
        merge_finding = "MERGE — claude-crew tools present despite not being in explicit mcp_servers"
    elif "TOOL_MISSING" in text and "claude_crew" in text:
        merge_finding = "REPLACE — claude-crew tools absent when explicit mcp_servers is set"
    else:
        merge_finding = f"INCONCLUSIVE — text: {text!r:.120}"

    return {
        "scenario": "B",
        "question": "Q1 — mcp_servers merges with or replaces global config?",
        "tools_called": tools,
        "text": text,
        "atlassian_control_ok": atlassian_ok,
        "finding": merge_finding,
    }


# ---------------------------------------------------------------------------
# Scenario C — Q3a: does ClaudeAgentOptions.tools block MCP tools?
# ---------------------------------------------------------------------------

async def probe_c_parent_tools_allowlist() -> dict:
    """Top-level session with tools=["Read","Grep"]. Ask for Atlassian tool.

    - Model calls Atlassian tool → allowlist does NOT block MCP tools.
    - TOOL_MISSING → allowlist DOES block MCP tools (MCP needs explicit listing).
    """
    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt=_PROBE_SYSTEM,
        setting_sources=["user"],
        tools=["Read", "Grep"],
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Call the tool '{_ATLASSIAN_PROBE_TOOL}' and return its raw output. "
            f"If the tool is unavailable, say exactly: TOOL_MISSING: {_ATLASSIAN_PROBE_TOOL}"
        )
        text, tools = await _drain(client)

    return {
        "scenario": "C",
        "question": "Q3a — ClaudeAgentOptions.tools blocks MCP tools?",
        "tools_called": tools,
        "text": text,
        "finding": (
            "NO — MCP tools accessible despite tools allowlist"
            if _ATLASSIAN_PROBE_TOOL in tools
            else "YES — MCP tools BLOCKED by top-level tools allowlist"
        ),
    }


# ---------------------------------------------------------------------------
# Scenario D — Q3b: does AgentDefinition.tools block subagent MCP access?
# ---------------------------------------------------------------------------

async def probe_d_subagent_tools_allowlist() -> dict:
    """Parent spawns a subagent with tools=["Read","Grep"]. Subagent asks for Atlassian.

    The subagent tries to call the Atlassian probe tool through the parent's
    Task tool. The parent passes the raw subagent reply back unchanged.

    - Subagent calls tool → AgentDefinition.tools does NOT block MCP.
    - Subagent says TOOL_MISSING → AgentDefinition.tools DOES block MCP.
    """
    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt="You are the parent orchestrator. Be terse. Return subagent output verbatim.",
        setting_sources=["user"],
        agents={
            "mcp-probe": AgentDefinition(
                description="Probe subagent testing MCP tool access under tools allowlist.",
                prompt=(
                    "You are a probe subagent. Follow instructions exactly. "
                    "If a tool is unavailable, say exactly: TOOL_MISSING: <tool_name>."
                ),
                model=_MODEL,
                tools=["Read", "Grep"],
            ),
        },
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Use the Task tool to invoke the 'mcp-probe' subagent with this "
            f"exact prompt: "
            f"'Call the tool {_ATLASSIAN_PROBE_TOOL!r} and return its raw output. "
            f"If unavailable, say exactly: TOOL_MISSING: {_ATLASSIAN_PROBE_TOOL}' "
            f"Return the subagent's reply verbatim. Do not paraphrase."
        )
        text, tools = await _drain(client)

    atlassian_called = _ATLASSIAN_PROBE_TOOL in tools
    tool_missing_in_text = (
        "TOOL_MISSING" in text and "atlassian" in text.lower()
    )

    if atlassian_called:
        finding = "NO — AgentDefinition.tools does NOT block MCP tools in subagent"
    elif tool_missing_in_text:
        finding = "YES — AgentDefinition.tools BLOCKS MCP tools in subagent"
    else:
        finding = f"INCONCLUSIVE — text: {text!r:.200}"

    return {
        "scenario": "D",
        "question": "Q3b — AgentDefinition.tools blocks subagent MCP access?",
        "tools_called": tools,
        "text": text,
        "finding": finding,
    }


# ---------------------------------------------------------------------------
# Scenario E — Q3c: do wildcard patterns work in AgentDefinition.tools?
# ---------------------------------------------------------------------------

async def probe_e_subagent_tools_wildcard() -> dict:
    """Subagent with tools=["Read","Grep","mcp__atlassian__*"]. Asks for Atlassian tool.

    Q3b confirmed that tools=["Read","Grep"] blocks MCP. If wildcards work,
    adding "mcp__atlassian__*" should re-enable the whole Atlassian namespace
    without needing to enumerate every tool by name.

    - Subagent calls tool → wildcards work (good UX for role definitions).
    - TOOL_MISSING → wildcards don't work (must enumerate every MCP tool by name).
    """
    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt="You are the parent orchestrator. Be terse. Return subagent output verbatim.",
        setting_sources=["user"],
        mcp_servers={"atlassian": {"type": "http", "url": _ATLASSIAN_URL}},
        agents={
            "mcp-wildcard-probe": AgentDefinition(
                description="Probe subagent testing MCP wildcard in tools allowlist.",
                prompt=(
                    "You are a probe subagent. Follow instructions exactly. "
                    "If a tool is unavailable, say exactly: TOOL_MISSING: <tool_name>."
                ),
                model=_MODEL,
                tools=["Read", "Grep", "mcp__atlassian__*"],
            ),
        },
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            f"Use the Task tool to invoke the 'mcp-wildcard-probe' subagent with this "
            f"exact prompt: "
            f"'Call the tool {_ATLASSIAN_PROBE_TOOL!r} and return its raw output. "
            f"If unavailable, say exactly: TOOL_MISSING: {_ATLASSIAN_PROBE_TOOL}' "
            f"Return the subagent's reply verbatim. Do not paraphrase."
        )
        text, tools = await _drain(client)

    atlassian_called = _ATLASSIAN_PROBE_TOOL in tools
    tool_missing_in_text = "TOOL_MISSING" in text and "atlassian" in text.lower()

    if atlassian_called:
        finding = "YES — wildcard 'mcp__atlassian__*' works in AgentDefinition.tools"
    elif tool_missing_in_text:
        finding = "NO — wildcard does NOT work; must enumerate MCP tools by name"
    else:
        finding = f"INCONCLUSIVE — text: {text!r:.200}"

    return {
        "scenario": "E",
        "question": "Q3c — wildcard 'mcp__atlassian__*' works in AgentDefinition.tools?",
        "tools_called": tools,
        "text": text,
        "finding": finding,
    }


# ---------------------------------------------------------------------------
# Runner + findings writer
# ---------------------------------------------------------------------------

def _fmt_result(r: dict) -> str:
    lines = [
        f"### Scenario {r['scenario']}: {r['question']}",
        f"",
        f"**Finding:** {r['finding']}",
        f"",
        f"Tools called: `{r['tools_called']}`",
        f"",
        f"Model text (truncated to 500 chars):",
        f"```",
        f"{r['text'][:500]}",
        f"```",
        f"",
    ]
    return "\n".join(lines)


async def main() -> int:
    _gated()

    print("Running scenario A — Q2 global MCP …")
    a = await probe_a_global_mcp_loads()
    print(f"  → {a['finding']}")

    print("Running scenario B — Q1 merge/replace …")
    b = await probe_b_merge_vs_replace()
    print(f"  → {b['finding']}")

    print("Running scenario C — Q3a parent tools allowlist …")
    c = await probe_c_parent_tools_allowlist()
    print(f"  → {c['finding']}")

    print("Running scenario D — Q3b subagent tools allowlist …")
    d = await probe_d_subagent_tools_allowlist()
    print(f"  → {d['finding']}")

    print("Running scenario E — Q3c wildcard in AgentDefinition.tools …")
    e = await probe_e_subagent_tools_wildcard()
    print(f"  → {e['finding']}")

    print()

    findings_path = Path(__file__).parent.parent / "doc" / "research" / "mcp-sdk-behavior.md"
    content = "\n".join([
        "# MCP SDK Behavior — Spike Findings",
        "",
        "Empirical results from `scripts/mcp_spike.py`.",
        "Re-run to refresh; do not edit by hand.",
        "",
        "## Summary",
        "",
        f"| Q | Finding |",
        f"|---|---------|",
        f"| Q2 — global MCP loads in SDK mode? | {a['finding']} |",
        f"| Q1 — mcp_servers merge or replace? | {b['finding']} |",
        f"| Q3a — parent tools allowlist blocks MCP? | {c['finding']} |",
        f"| Q3b — subagent tools allowlist blocks MCP? | {d['finding']} |",
        f"| Q3c — wildcard works in AgentDefinition.tools? | {e['finding']} |",
        "",
        "## Detail",
        "",
        _fmt_result(a),
        _fmt_result(b),
        _fmt_result(c),
        _fmt_result(d),
        _fmt_result(e),
    ])
    findings_path.write_text(content)
    print(f"Findings written to {findings_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
