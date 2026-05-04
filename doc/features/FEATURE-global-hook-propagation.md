# Feature: Global Hook Propagation for SDK Teammates

## Problem

Shell command hooks defined in `~/.claude/settings.json` (`PreToolUse`, `PostToolUse`,
`SessionStart`, etc.) do not execute inside SDK teammate sessions. The SDK uses Python-level
`HookMatcher` callables registered via `ClaudeAgentOptions.hooks`; it loads the settings
file but does not translate or run shell command hooks from it.

Consequence: ambient tools that work via hooks in the lead session — rtk's Bash output
filtering, custom pre/post tool wrappers, session-start injectors — are silently absent
from every spawned teammate. No error surfaces. The operator has no way to opt in without
modifying claude-crew internals.

Empirically confirmed 2026-05-04 via `tests/test_live_global_tools_probe.py`:
- `PreToolUse` Bash hook (`rtk hook claude`) — did **not** fire in SDK session
- `SessionStart` hook (plugin-injected) — **did** fire (plugin hooks are a different path)
- `enabledPlugins` tools — **are** auto-available (separate from shell hooks)

## Goal

Allow the operator to declare, via configuration, which shell command hooks from
`~/.claude/settings.json` should be automatically translated into SDK-level Python
wrappers and applied to every spawned teammate — without the lead having to pass
anything at `spawn_teammate` time.

## Design

### Config source

A new key under the existing `claudeCrew` namespace in `~/.claude.json`:

```json
{
  "claudeCrew": {
    "propagateHooks": [
      {
        "event": "PreToolUse",
        "matcher": "Bash",
        "command": "rtk hook claude"
      }
    ]
  }
}
```

`propagateHooks` is a list of hook entries. Each entry must specify:
- `event` — one of `PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`
- `command` — the shell command to run (same value as in `settings.json`)
- `matcher` — optional tool name filter (only meaningful for `PreToolUse`/`PostToolUse`)

Entries without `matcher` apply to all tool uses for that event type.

Alternatively, `"propagateHooks": true` propagates every shell command hook found in
`~/.claude/settings.json` automatically. Decision on which form to ship first is deferred
to implementation; the explicit list form is safer and easier to reason about.

### Hook wrapper contract

For each entry, a Python async wrapper is created at factory startup:

```python
async def _shell_hook_wrapper(inp: dict, tool_use_id: str, ctx: dict) -> dict:
    env = {
        **os.environ,
        "CLAUDE_HOOK_EVENT":  event,           # e.g. "PreToolUse"
        "CLAUDE_TOOL_NAME":   inp.get("tool_name", ""),
        "CLAUDE_TOOL_INPUT":  json.dumps(inp.get("tool_input", {})),
        "CLAUDE_TOOL_USE_ID": tool_use_id,
    }
    result = subprocess.run(command, shell=True, env=env,
                            capture_output=True, text=True, timeout=hook_timeout)
    if result.returncode != 0:
        logger.warning("propagated hook exited %d: %s", result.returncode, result.stderr)
        return {}
    # Parse stdout for tool_input rewrite (JSON object with "tool_input" key)
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
            if "tool_input" in parsed:
                return {"tool_input": parsed["tool_input"]}
        except json.JSONDecodeError:
            pass
    return {}
```

Key points:
- Env vars replicate the contract that shell hooks expect from Claude Code (`CLAUDE_TOOL_NAME`,
  `CLAUDE_TOOL_INPUT`, `CLAUDE_HOOK_EVENT`). These are normally absent in SDK mode — the
  wrapper injects them explicitly from `inp`.
- If the shell command outputs JSON with a `tool_input` key, that value is returned to the
  SDK as a tool input rewrite. This is how rtk's command rewriting propagates.
- Non-zero exit or non-JSON output: logged as warning, empty dict returned (no-op).
- Timeout is configurable; default 5s (same order of magnitude as the existing telemetry
  hook timeout of 1s, but shell commands need more headroom).

### SDK tool_input rewrite: works, gated by permission mode

Empirically confirmed 2026-05-04 via `TestSdkHookRewriteProbe` in
`test_live_global_tools_probe.py`.

**Correct return format for input rewriting:**

```python
return {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "updatedInput": {"command": "rewritten command here"},
    }
}
```

**Permission mode constraint:** `updatedInput` is honored only when the subprocess is NOT
running in `bypassPermissions` mode. In bypass mode the hook fires (for side effects) but
all control output — `permissionDecision`, `updatedInput` — is discarded by the CLI.

SDK teammates inherit `bypassPermissions` from the user's `~/.claude/settings.json`
(`defaultMode: "bypassPermissions"`). A propagated rewriting hook therefore has no effect
unless the teammate is spawned with an explicit non-bypass `permission_mode`.

**Implication for the feature:**

Rewriting hooks work correctly when the operator:
1. Spawns the teammate with `permission_mode="default"` (or `"acceptEdits"`), AND
2. Includes `permissionDecision: "allow"` in the hook return so the tool isn't blocked

For operators running in `bypassPermissions` (the common case), rewriting hooks produce
no visible effect — tools execute with their original inputs. The feature should document
this constraint prominently. Operators who want rtk-style rewriting must opt out of
bypass mode for those teammates, or use the shell alias injection fallback instead.

### Integration point

Propagated hook wrappers are built once in `factories.py` at startup (same time as the
merged pack and always-on tools are read). They are stored in the factory closure and
merged into `opts_kwargs["hooks"]` in `SdkTeammate._run()` before `ClaudeAgentOptions`
is constructed:

```python
# sdk_teammate.py _run()
for event, matchers in propagated_hooks.items():
    existing = opts_kwargs["hooks"].setdefault(event, [])
    existing.extend(matchers)   # telemetry hooks first, propagated hooks appended
```

The `HookMatcher` for each propagated entry uses the `matcher` field from config (tool
name string or `None` for all tools) and wraps the shell command callable.

### SessionStart hooks

`SessionStart` entries in `propagateHooks` are registered differently — no `inp`/`tool_use_id`
args. The wrapper signature simplifies to `async def _session_start_wrapper() -> None`.
Env vars still injected where meaningful (`CLAUDE_HOOK_EVENT=SessionStart`).

Note: plugin-registered `SessionStart` hooks (e.g. context-mode's cache healer) already
fire automatically in SDK sessions — they do not need to be listed in `propagateHooks`.
Only shell command hooks defined directly in `settings.json` are silent in SDK mode and
need propagation.

### Scope and non-goals

- Only hooks listed in `propagateHooks` are translated. No automatic wholesale propagation
  of all settings.json hooks (some hooks assume interactive context and would misbehave).
- `propagateHooks: true` (auto-propagate all) is a stretch goal, not v1.
- Hook output that references tool *switching* (redirecting Bash → Read) is not supported
  in v1. Only tool input rewriting (`tool_input` JSON key) and side-effect-only hooks
  (no return) are handled.
- The wrapper does not support hooks that read from stdin. Command-line-only hooks only.

## Acceptance Criteria

- `claudeCrew.propagateHooks` is read from `~/.claude.json` at `default_factory` startup.
- Each listed entry is translated into a Python async wrapper registered via `HookMatcher`
  in `ClaudeAgentOptions.hooks`.
- Wrapper correctly injects `CLAUDE_TOOL_NAME`, `CLAUDE_TOOL_INPUT`, `CLAUDE_HOOK_EVENT`,
  `CLAUDE_TOOL_USE_ID` env vars before running the shell command.
- If the shell command outputs JSON containing a `updatedInput` key, the wrapper returns it
  via the correct SDK format: `{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "allow", "updatedInput": {...}}}`. This rewrite is honored by the
  CLI only when the teammate is NOT in `bypassPermissions` mode.
- Non-zero exit or non-JSON stdout: warning logged, no-op returned. Teammate turn continues.
- Propagated hooks are appended after existing telemetry hooks in the same event list.
- `SessionStart` hook entries are supported with a simplified no-arg wrapper.
- Config absent or empty list: no behavior change.
- `spawn_teammate` `extra_tools` and pack-level config are unaffected — propagated hooks
  are additive.

## Tests

- Unit: config parsing → correct `HookMatcher` objects built (stub wrapper, no subprocess).
- Unit: wrapper env var injection — assert subprocess receives correct env.
- Unit: wrapper return value parsing — JSON with `tool_input`, non-JSON, non-zero exit.
- Live (gated `CLAUDE_CREW_LIVE_TESTS=1`): extend `test_live_global_tools_probe.py` with
  a new `TestPropagatedHookProbe` class that plants a test hook entry in config, spawns
  a teammate, triggers it, and asserts the side effect (e.g. a marker file written by
  the shell command, or rtk command count increase).
- Live probe for SDK tool_input rewrite support (prerequisite to verifying rtk rewriting).
