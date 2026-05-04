"""Probe: hook + plugin integration in SDK teammate sessions.

These are EMPIRICAL DISCOVERY tests — they document what actually works,
not what we expect. Results drive the global-tools feature spec.

Questions under test:

  Q1. Does rtk's PreToolUse hook from ~/.claude/settings.json fire inside
      SDK teammate Bash calls?
      Registered as: PreToolUse → matcher: Bash → command: "rtk hook claude"
      Measurement: rtk global command count before vs after a teammate Bash call.

  Q2. Are context-mode plugin tools auto-available in SDK sessions without
      extra config?
      context-mode is registered as an enabledPlugin (not in mcpServers).

  Q4. Does the SDK's PreToolUse Python hook return value support tool_input
      rewrite? If a hook returns {"tool_input": {"command": "..."}} does the
      SDK actually run the rewritten command instead of the original?
      Critical prerequisite for rtk command rewriting via propagated hooks.
      If plugins load in SDK subprocesses, tools should appear unprompted.

  Q3. Does the SessionStart hook run at SDK session start?
      Registered as: SessionStart → context-mode-cache-heal.mjs
      If it runs, the teammate should report receiving context management
      instructions.

Run individually to see printed findings:
  CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_global_tools_probe.py -v -s

Each assertion failure IS a finding — the message explains the architectural
implication for the global-tools feature design.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_crew.broker import LEAD_ID, Broker
from claude_crew.envelope import Envelope, new_message_id
from claude_crew.factories import sdk_factory
from claude_crew.sdk_teammate import SdkTeammate


pytestmark = pytest.mark.skipif(
    os.environ.get("CLAUDE_CREW_LIVE_TESTS") != "1",
    reason="live API gated; set CLAUDE_CREW_LIVE_TESTS=1 to run",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rtk_command_count() -> int | None:
    """Return rtk's current total command count, or None if rtk is unavailable."""
    if not shutil.which("rtk"):
        return None
    try:
        result = subprocess.run(
            ["rtk", "gain"],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"Total commands:\s+([\d,]+)", result.stdout)
        if m:
            return int(m.group(1).replace(",", ""))
    except Exception:
        pass
    return None


def _context_mode_installed() -> bool:
    base = Path("~/.claude/plugins/cache/context-mode/context-mode").expanduser()
    return base.is_dir() and any(base.iterdir())


async def _wait_for_lead(broker: Broker, count: int, timeout: float = 120.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if len(broker.get_messages(recipient=LEAD_ID)) >= count:
            return
        await asyncio.sleep(0.5)
    raise AssertionError(
        f"timed out waiting for {count} lead messages; "
        f"got {len(broker.get_messages(recipient=LEAD_ID))}"
    )


async def _ask(broker: Broker, tid: str, prompt: str, n: int) -> str:
    await broker.send(Envelope(
        id=new_message_id(), seq=0,
        sender=LEAD_ID, recipient=tid, timestamp=0.0,
        payload=prompt,
    ))
    await _wait_for_lead(broker, n)
    msg = broker.get_messages(recipient=LEAD_ID)[-1]
    if isinstance(msg.payload, dict):
        return msg.payload.get("text", "") or msg.payload.get("message", "")
    return str(msg.payload)


@pytest.fixture
async def broker():
    b = Broker()
    yield b
    await b.shutdown_all()


# ---------------------------------------------------------------------------
# Q1 — rtk hook
# ---------------------------------------------------------------------------

class TestRtkHookProbe:
    """Q1: Does the rtk PreToolUse hook fire inside SDK teammate Bash calls?

    ~/.claude/settings.json registers:
      PreToolUse → matcher: Bash → command: "rtk hook claude"

    SDK teammates are spawned with setting_sources=["user", "project"], which
    should make the subprocess read ~/.claude/settings.json. Whether hooks
    defined there actually execute is what this test discovers.

    A count increase proves the hook ran. No change proves SDK subprocesses
    read the settings file but skip hook execution — or don't read it at all.
    """

    @pytest.mark.skipif(
        not shutil.which("rtk"),
        reason="rtk not on PATH — cannot measure hook firing",
    )
    async def test_rtk_hook_fires_for_teammate_bash(self, broker: Broker) -> None:
        before = _rtk_command_count()
        assert before is not None, "could not read rtk baseline"

        tid = await broker.spawn_teammate(
            role="general-purpose", name=None, factory=sdk_factory,
        )

        # One Bash call, minimally ambiguous.
        text = await _ask(
            broker, tid,
            "Run `git rev-parse HEAD` via Bash and report only the exact hash. "
            "No explanation, just the hash.",
            1,
        )
        assert text, "teammate returned empty response"

        after = _rtk_command_count()
        assert after is not None, "could not read rtk post-count"

        fired = after > before
        print(
            f"\nQ1 — rtk hook {'FIRED' if fired else 'DID NOT FIRE'} "
            f"in SDK session: count {before} → {after}"
        )

        assert fired, (
            f"rtk PreToolUse hook did NOT fire inside SDK teammate session "
            f"(command count unchanged at {before}). "
            "IMPLICATION: ~/.claude/settings.json hooks do not execute inside SDK "
            "subprocess sessions. Hook-based tools (rtk) will need explicit invocation "
            "instructions injected into teammate system prompts — they cannot rely on "
            "transparent hook interception."
        )


# ---------------------------------------------------------------------------
# Q2 + Q3 — context-mode plugin
# ---------------------------------------------------------------------------

class TestContextModePluginProbe:
    """Q2 + Q3: Do context-mode plugin tools and hooks reach SDK sessions?

    context-mode is NOT registered in ~/.claude.json mcpServers. It loads as
    a Claude Code plugin via enabledPlugins in ~/.claude/settings.json. This
    test probes whether that plugin mechanism extends to SDK subprocess sessions.

    Two sub-probes:
      Q2 — tool availability: can the teammate call ctx_* tools unprompted?
      Q3 — SessionStart hook: did the session-start script inject instructions?
    """

    @pytest.mark.skipif(
        not _context_mode_installed(),
        reason="context-mode plugin not installed locally — run /ctx-upgrade first",
    )
    async def test_context_mode_tools_available(self, broker: Broker) -> None:
        """Q2: Are ctx_* tools auto-available without explicit mcpServers config?"""
        tid = await broker.spawn_teammate(
            role="general-purpose", name=None, factory=sdk_factory,
        )

        text = await _ask(
            broker, tid,
            "List ALL MCP tool names available to you that contain 'ctx' or "
            "'context_mode'. If none match, say exactly 'NONE FOUND'. "
            "List only the tool names, one per line.",
            1,
        )
        assert text, "teammate returned empty response"

        has_ctx_tools = any(
            s in text.lower() for s in ("ctx_", "context_mode", "mcp__plugin_context")
        )
        print(
            f"\nQ2 — context-mode plugin tools "
            f"{'ARE' if has_ctx_tools else 'ARE NOT'} "
            f"auto-available in SDK sessions.\nResponse excerpt: {text[:300]}"
        )

        assert has_ctx_tools, (
            "context-mode plugin tools are NOT auto-available in SDK teammate sessions "
            "even though the plugin is listed in enabledPlugins in settings.json. "
            "IMPLICATION: The Claude Code plugin mechanism does not apply to SDK "
            "subprocess sessions. To surface context-mode tools in teammates, claude-crew "
            "would need to either: (a) register context-mode as a standalone MCP server "
            "entry in ~/.claude.json and wire it via pack mcpServers + allowed_tools, "
            "or (b) run context-mode as a sidecar MCP process and inject its config "
            "at teammate spawn time."
        )

    @pytest.mark.skipif(
        not _context_mode_installed(),
        reason="context-mode plugin not installed locally — run /ctx-upgrade first",
    )
    async def test_session_start_hook_fired(self, broker: Broker) -> None:
        """Q3: Did the SessionStart hook run and inject context-mode instructions?

        The hook runs context-mode-cache-heal.mjs. If it fires, the teammate
        should report receiving a 'tool selection hierarchy' or similar guidance.
        """
        tid = await broker.spawn_teammate(
            role="general-purpose", name=None, factory=sdk_factory,
        )

        text = await _ask(
            broker, tid,
            "At the very start of this session, did you receive any special "
            "instructions about context management, a 'tool selection hierarchy', "
            "or context-mode tools (ctx_batch_execute, ctx_search, etc.)? "
            "Answer YES or NO first, then describe what you received.",
            1,
        )
        assert text, "teammate returned empty response"

        hook_visible = (
            text.lower().startswith("yes")
            or "tool selection" in text.lower()
            or "ctx_" in text.lower()
            or "context-mode" in text.lower()
        )
        print(
            f"\nQ3 — SessionStart hook effect "
            f"{'VISIBLE' if hook_visible else 'NOT VISIBLE'} "
            f"in SDK session.\nResponse: {text[:400]}"
        )

        assert hook_visible, (
            "The SessionStart hook (context-mode-cache-heal.mjs) produced no visible "
            "effect in the SDK teammate session — the teammate received no context-mode "
            "instructions at session start. "
            "IMPLICATION: SessionStart hooks from settings.json do not execute (or do "
            "not inject system-reminders) inside SDK subprocess sessions. Global "
            "session-start behavior cannot rely on the hook mechanism and must be "
            "embedded directly in teammate system prompts via the global-tools feature."
        )


# ---------------------------------------------------------------------------
# Q4 — SDK tool_input rewrite support
# ---------------------------------------------------------------------------

class TestSdkHookRewriteProbe:
    """Q4: Does returning {"tool_input": {...}} from a Python PreToolUse hook
    cause the SDK to run the rewritten command instead of the original?

    This is the critical prerequisite for rtk command rewriting via propagated
    hooks. We inject a Python PreToolUse hook that unconditionally rewrites any
    Bash command to `echo HOOK_REWRITE_CONFIRMED`, then ask the teammate to run
    `git status`. If the response contains the sentinel, the SDK honors rewrites.

    Uses a custom factory that patches _on_pre_tool_use on the SdkTeammate
    instance before start() is called. _run() references self._on_pre_tool_use
    by name at execution time, so the patch takes effect when hooks are registered.
    """

    async def test_pretooluse_hook_can_rewrite_tool_input(
        self, broker: Broker,
    ) -> None:
        SENTINEL = "HOOK_REWRITE_CONFIRMED"

        def _rewriting_factory(id: str, name: str, role: str, **kwargs) -> SdkTeammate:
            tm = sdk_factory(id, name, role, **kwargs)
            original_pre = tm._on_pre_tool_use

            async def _rewriting_pre(inp: dict, tool_use_id: str, ctx: dict) -> dict:
                await original_pre(inp, tool_use_id, ctx)
                if inp.get("tool_name") == "Bash":
                    # Correct SDK return format for tool_input rewriting.
                    # hookSpecificOutput.updatedInput is honored when permission_mode != bypassPermissions.
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "allow",
                            "updatedInput": {"command": f"echo {SENTINEL}"},
                        }
                    }
                return {}

            tm._on_pre_tool_use = _rewriting_pre  # type: ignore[method-assign]
            return tm

        _rewriting_factory.requires_auth = True  # type: ignore[attr-defined]

        # bypassPermissions ignores updatedInput — use "default" to test rewrite.
        tid = await broker.spawn_teammate(
            role="general-purpose", name=None, factory=_rewriting_factory,
            permission_mode="default",
        )

        text = await _ask(
            broker, tid,
            "Run `git status` via Bash and report the exact output verbatim. "
            "Do not summarize — show exactly what the command printed.",
            1,
        )
        assert text, "teammate returned empty response"

        rewrote = SENTINEL in text
        print(
            f"\nQ4 — SDK PreToolUse hook tool_input rewrite "
            f"{'IS HONORED' if rewrote else 'IS NOT HONORED'}.\n"
            f"Response excerpt: {text[:300]}"
        )

        assert rewrote, (
            f"The SDK did NOT honor the tool_input rewrite returned by the Python "
            f"PreToolUse hook (sentinel '{SENTINEL}' absent from response). "
            "IMPLICATION: rtk command rewriting cannot be propagated via the hook "
            "return value. Fallback required: inject system prompt instructions "
            "telling teammates to prefix heavy commands with 'rtk', OR use shell "
            "alias injection via the teammate subprocess environment."
        )
