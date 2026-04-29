"""Empirical spike: do global shell-command hooks fire in SDK mode?

Probes the open question from doc/BACKLOG.md ("Hooks: two systems, two answers"):

  Q1. Do PostToolUse hooks configured in ~/.claude/settings.json fire when
      an SDK teammate subprocess runs (CLAUDE_CODE_ENTRYPOINT=sdk-py)?

  Q2. Do PreToolUse hooks fire in SDK mode?

  Q3. Do hooks fire for subagent tool calls (Task tool) inside an SDK session,
      or only for top-level session tools?

Method:
  1. Back up ~/.claude/settings.json.
  2. Inject a PostToolUse hook (and PreToolUse hook) that append a JSON line to
     a temp log file when triggered.
  3. Run an SDK session that uses the Bash tool (top-level).
  4. Run a second SDK session that spawns a subagent and has the subagent use Bash.
  5. Restore settings.json.
  6. Inspect the log file.

Gate: CLAUDE_CREW_LIVE_TESTS=1 in environment.
Cost: ~$0.05–0.10 (haiku, minimal turns).

Run: `CLAUDE_CREW_LIVE_TESTS=1 uv run python scripts/hooks_spike.py`

Findings written to doc/research/hooks-sdk-behavior.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AgentDefinition,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
_MODEL = "claude-haiku-4-5-20251001"

_PROBE_SYSTEM = (
    "You are a minimal probe session. Follow instructions exactly. "
    "Run exactly the tool you are asked to run. Be terse."
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


# ---------------------------------------------------------------------------
# Settings patching — backup / inject / restore
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    try:
        return json.loads(_SETTINGS_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_settings(data: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(data, indent=2) + "\n")


class _SettingsPatch:
    """Context manager: inject probe hooks, restore on exit."""

    def __init__(self, log_path: str) -> None:
        self._log_path = log_path
        self._original: dict = {}

    def __enter__(self) -> "_SettingsPatch":
        self._original = _load_settings()
        patched = json.loads(json.dumps(self._original))  # deep copy

        # Hook command: dump full env to a per-invocation file, then append
        # a sentinel line to the main log. Per-invocation file avoids any
        # concurrent-write race between hook processes.
        hook_cmd = (
            f'env > {self._log_path}.$$.env ; '
            f'echo ---HOOK-$$ >> {self._log_path}'
        )

        probe_hooks = {
            "PreToolUse": [{"hooks": [{"type": "command", "command": hook_cmd}]}],
            "PostToolUse": [{"hooks": [{"type": "command", "command": hook_cmd}]}],
        }

        existing_hooks = patched.get("hooks", {})
        # Merge: prepend probe matchers to any existing hook lists.
        for event, matchers in probe_hooks.items():
            existing_hooks[event] = matchers + existing_hooks.get(event, [])
        patched["hooks"] = existing_hooks

        _save_settings(patched)
        print(f"  [patch] probe hooks injected → log: {self._log_path}")
        return self

    def __exit__(self, *_) -> None:
        _save_settings(self._original)
        print("  [patch] settings.json restored")


# ---------------------------------------------------------------------------
# Scenario A — Q1/Q2: do Pre/PostToolUse hooks fire in top-level SDK session?
# ---------------------------------------------------------------------------

async def probe_a_top_level_hooks(log_path: str) -> dict:
    """Top-level SDK session uses Bash. Do hooks fire?"""
    log_before = _read_log_lines(log_path)

    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt=_PROBE_SYSTEM,
        setting_sources=["user"],
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query('Run: `echo probe_a_done`')
        text, tools = await _drain(client)

    log_after = _read_log_lines(log_path)
    new_lines = log_after[len(log_before):]

    hook_count = sum(1 for l in new_lines if l.startswith("---HOOK"))
    pre_fired = any("PreToolUse" in l for l in new_lines)
    post_fired = any("PostToolUse" in l for l in new_lines)
    bash_fired = any("Bash" in l for l in new_lines)

    return {
        "scenario": "A",
        "question": "Q1/Q2 — Pre/PostToolUse hooks fire in top-level SDK session?",
        "tools_called": tools,
        "new_log_lines": new_lines,
        "hook_invocations": hook_count,
        "finding": _hook_finding(new_lines, pre_fired, post_fired, bash_fired, hook_count),
    }


# ---------------------------------------------------------------------------
# Scenario B — Q3: do hooks fire for subagent tool calls?
# ---------------------------------------------------------------------------

async def probe_b_subagent_hooks(log_path: str) -> dict:
    """Parent spawns subagent; subagent uses Bash. Do hooks fire for the subagent?"""
    log_before = _read_log_lines(log_path)

    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt="You are an orchestrator. Return subagent output verbatim.",
        setting_sources=["user"],
        agents={
            "bash-probe": AgentDefinition(
                description="Probe subagent that runs a Bash command.",
                prompt="You run exactly the Bash command you are given. Be terse.",
                model=_MODEL,
                tools=["Bash"],
            ),
        },
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Use the Task tool to invoke the 'bash-probe' subagent with this "
            "exact prompt: 'Run: `echo probe_b_done`'. Return its output verbatim."
        )
        text, tools = await _drain(client)

    log_after = _read_log_lines(log_path)
    new_lines = log_after[len(log_before):]

    hook_count = sum(1 for l in new_lines if l.startswith("---HOOK"))
    pre_fired = any("PreToolUse" in l for l in new_lines)
    post_fired = any("PostToolUse" in l for l in new_lines)
    bash_fired = any("Bash" in l for l in new_lines)

    return {
        "scenario": "B",
        "question": "Q3 — hooks fire for subagent tool calls inside SDK session?",
        "tools_called": tools,
        "new_log_lines": new_lines,
        "hook_invocations": hook_count,
        "finding": _hook_finding(new_lines, pre_fired, post_fired, bash_fired, hook_count),
    }


# ---------------------------------------------------------------------------
# Scenario E — Q4: do matchers filter correctly in SDK mode?
# ---------------------------------------------------------------------------

class _MatchedSettingsPatch:
    """Inject a Bash-only matched hook. No matcher on Read."""

    def __init__(self, log_path: str) -> None:
        self._log_path = log_path
        self._original: dict = {}

    def __enter__(self) -> "_MatchedSettingsPatch":
        self._original = _load_settings()
        patched = json.loads(json.dumps(self._original))

        hook_cmd = (
            f'env > {self._log_path}.$$.env ; '
            f'echo ---HOOK-$$ >> {self._log_path}'
        )

        # Only match Bash — Read calls should NOT trigger this hook.
        matched_hooks = {
            "PostToolUse": [{
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": hook_cmd}],
            }],
        }

        existing_hooks = patched.get("hooks", {})
        for event, matchers in matched_hooks.items():
            existing_hooks[event] = matchers + existing_hooks.get(event, [])
        patched["hooks"] = existing_hooks

        _save_settings(patched)
        print(f"  [patch] Bash-only matched hook injected → log: {self._log_path}")
        return self

    def __exit__(self, *_) -> None:
        _save_settings(self._original)
        print("  [patch] settings.json restored")


async def probe_e_matcher_filtering(log_path: str) -> dict:
    """Session uses both Bash and Read. Hook has matcher='Bash'.

    Expected outcomes:
      - 1 invocation → matcher works (only Bash triggered PostToolUse hook)
      - 2 invocations → matcher ignored (both Bash and Read triggered hook)
      - 0 invocations → matcher blocks all hooks in SDK mode
    """
    log_before = _read_log_lines(log_path)

    options = ClaudeAgentOptions(
        model=_MODEL,
        system_prompt=_PROBE_SYSTEM,
        setting_sources=["user"],
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Do exactly two things in order:\n"
            "1. Run `echo probe_e_bash` using the Bash tool.\n"
            "2. Read the file /etc/hostname using the Read tool.\n"
            "Report both results."
        )
        text, tools = await _drain(client)

    # Small delay to let any async hook processes flush their writes.
    await asyncio.sleep(1.0)

    log_after = _read_log_lines(log_path)
    new_lines = log_after[len(log_before):]
    hook_count = sum(1 for l in new_lines if l.startswith("---HOOK"))

    bash_called = "Bash" in tools
    read_called = "Read" in tools

    if not bash_called or not read_called:
        finding = f"INCONCLUSIVE — model did not call both tools (called: {tools})"
    elif hook_count == 0:
        finding = "MATCHER BLOCKS ALL — 0 hooks fired despite Bash call (matcher broke SDK hooks)"
    elif hook_count == 1:
        finding = "MATCHER WORKS — 1 hook fired (Bash only, Read correctly excluded)"
    elif hook_count == 2:
        finding = "MATCHER IGNORED — 2 hooks fired (both Bash and Read triggered the Bash-only hook)"
    else:
        finding = f"UNEXPECTED — {hook_count} hooks fired for 2 tool calls"

    return {
        "scenario": "E",
        "question": "Q4 — do matchers filter correctly in SDK mode?",
        "tools_called": tools,
        "new_log_lines": new_lines,
        "hook_invocations": hook_count,
        "finding": finding,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_log_lines(log_path: str) -> list[str]:
    try:
        return Path(log_path).read_text().splitlines()
    except FileNotFoundError:
        return []


def _hook_finding(lines: list[str], pre: bool, post: bool, bash: bool, hook_count: int) -> str:
    if hook_count == 0:
        return "NO HOOKS FIRED — log has no ---HOOK sentinels (hooks do not run in SDK mode)"
    parts = [f"{hook_count} hook invocation(s) confirmed"]
    if pre:
        parts.append("PreToolUse env var present")
    if post:
        parts.append("PostToolUse env var present")
    if bash:
        parts.append("Bash tool name observed")
    return "HOOKS FIRED — " + ", ".join(parts)


def _fmt_result(r: dict) -> str:
    lines = [
        f"### Scenario {r['scenario']}: {r['question']}",
        "",
        f"**Finding:** {r['finding']}",
        "",
        f"Tools called by model: `{r['tools_called']}`",
        "",
        "New log lines from hook probe:",
        "```",
        "\n".join(r["new_log_lines"]) if r["new_log_lines"] else "(none)",
        "```",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def main() -> int:
    _gated()

    with tempfile.NamedTemporaryFile(
        prefix="claude_crew_hooks_probe_", suffix=".log", delete=False
    ) as f:
        log_path = f.name

    print(f"Probe log: {log_path}")

    with _SettingsPatch(log_path):
        print("Running scenario A — top-level session hooks …")
        a = await probe_a_top_level_hooks(log_path)
        print(f"  → {a['finding']}")

        print("Running scenario B — subagent hooks …")
        b = await probe_b_subagent_hooks(log_path)
        print(f"  → {b['finding']}")

    with _MatchedSettingsPatch(log_path):
        print("Running scenario E — conditional hook (Bash matcher) …")
        e = await probe_e_matcher_filtering(log_path)
        print(f"  → {e['finding']}")

    findings_path = Path(__file__).parent.parent / "doc" / "research" / "hooks-sdk-behavior.md"
    content = "\n".join([
        "# Hooks SDK Behavior — Spike Findings",
        "",
        "Empirical results from `scripts/hooks_spike.py`.",
        "Re-run to refresh; do not edit by hand.",
        "",
        "## Summary",
        "",
        "| Q | Finding |",
        "|---|---------|",
        f"| Q1 — PreToolUse fires in SDK mode? | {a['finding']} |",
        f"| Q2 — PostToolUse fires in SDK mode? | {a['finding']} |",
        f"| Q3 — hooks fire for subagent tool calls? | {b['finding']} |",
        f"| Q4 — matchers filter correctly in SDK mode? | {e['finding']} |",
        "",
        "## Detail",
        "",
        _fmt_result(a),
        _fmt_result(b),
        _fmt_result(e),
    ])
    findings_path.write_text(content)
    print(f"\nFindings written to {findings_path}")
    print(f"Raw probe log at {log_path} (not deleted — inspect if needed)")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
