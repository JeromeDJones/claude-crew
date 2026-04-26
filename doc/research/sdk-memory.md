# Claude Agent SDK — Memory & Persistence Behavior

**Created:** 2026-04-25
**Source:** Feature #2 SC-5 spike. Empirical findings from running live tests
against `claude-agent-sdk` v0.1.68, plus source-code reading inside
`.venv/lib/python3.12/site-packages/claude_agent_sdk/`.

This doc resolves the "SDK memory behavior" verification item from
`PRODUCT-VISION.md`. The other two verification items (subagent context
isolation, per-subagent token budgets) are gated behind Feature #3a and
not addressed here.

---

## 1. Conversation persistence within a session

**Answer: automatic.** Multiple `query()` calls on the same `ClaudeSDKClient`
instance, with the same `session_id`, share conversation history.

**Source evidence:**
- `claude_agent_sdk/_internal/transport/subprocess_cli.py:32–62` — `SubprocessCLITransport` holds a single subprocess reference (`self._process`) for the lifetime of the connection. The transport is opened on `__aenter__` (via `client.connect()`) and closed on `__aexit__`.
- `claude_agent_sdk/client.py:282–310` — `query()` writes a message to the persistent transport's stdin with the supplied `session_id`. The Claude Code CLI subprocess maintains conversation state internally; the SDK does not pass message history on each call.

**Empirical evidence:**
- `tests/test_live_sdk.py::TestUUIDRecallOver10Turns::test_uuid_recall` — turn 1 plants a fresh UUIDv4 token. Turns 2–9 are unrelated chatter (math, geography, spelling). Turn 10 asks the model to repeat the token verbatim. Asserts exact substring match. Passes when `CLAUDE_CREW_LIVE_TESTS=1`.

**Implication for SdkTeammate:**
We rely on this behavior. `SdkTeammate._handle_one_turn` calls `client.query(prompt, session_id="default")` for every inbound envelope on the same client instance. No manual history tracking required. One teammate = one client = one persistent conversation.

---

## 2. CLAUDE.md loading

**Answer: loaded by the Claude CLI's own defaults, not gated by `setting_sources`.**
This is different from what the SDK source-reading suggested. Empirical evidence
overrides docs.

**Source evidence (the SDK side):**
- `claude_agent_sdk/_internal/transport/subprocess_cli.py:165–201` — `_apply_skills_defaults()`. If `options.skills` is non-`None` and `options.setting_sources` is `None`, the SDK auto-injects `setting_sources=["user", "project"]`. Otherwise, no `--setting-sources` flag is passed to the CLI subprocess.
- `claude_agent_sdk/_internal/transport/subprocess_cli.py:328–329` — the `--setting-sources` CLI flag is only appended if resolved non-`None`.
- The SDK source thus controls whether the `--setting-sources` flag reaches the CLI; it does *not* control what the CLI does when the flag is absent.

**Empirical evidence (what the CLI actually does):**
- `TestClaudeMdLoading::test_claude_md_loads_with_setting_sources` — `setting_sources=["user", "project"]`, asks "what name is the human called?" → response contains "jerome" or "kael". CLAUDE.md was loaded. ✓
- A second probe with `setting_sources=None`, asking "do you have access to a CLAUDE.md that names a specific user?" → response: **"yes"**. CLAUDE.md was *also* loaded.
- Conclusion: the Claude CLI's default behavior — when invoked without `--setting-sources` — already includes loading `~/.claude/CLAUDE.md` and project-local `CLAUDE.md`. The SDK's `setting_sources=None` does not suppress it; it just declines to *override* the default.

**To suppress CLAUDE.md loading** would require either: (a) launching the CLI with a `CLAUDE_CONFIG_DIR` pointed elsewhere, (b) passing `setting_sources=[]` explicitly (untested — behavior may differ from `None`), or (c) a future SDK option not yet present. None of these are needed for v1; we *want* CLAUDE.md loaded.

**Implication for SdkTeammate:**
Default `setting_sources=["user", "project"]` is preserved as the explicit, declared default — even though it doesn't change CLAUDE.md behavior, it keeps intent clear and protects against future SDK changes that might gate CLAUDE.md on this flag. Operators get a teammate that already knows the standing instructions in `~/.claude/CLAUDE.md`, which is the right default for the "feels like a collaborator" experience.

Files loaded by default:
- `~/.claude/CLAUDE.md` — yes, via CLI default
- `<cwd>/CLAUDE.md` — yes, via CLI default

---

## 3. Auto-memory subsystem (`~/.claude/projects/.../memory/`)

**Answer: not active for SDK programs.** The SDK does not read from or write to
`~/.claude/projects/<encoded-cwd>/memory/MEMORY.md` or any path under that tree.

**Source evidence:**
- `claude_agent_sdk/_internal/sessions.py:1–10` — the only SDK module that touches `~/.claude/projects/` reads `.jsonl` session files for session resumption. No memory-file access.
- `grep -r "memory\|MEMORY.md" .venv/lib/python3.12/site-packages/claude_agent_sdk/` — only references are to `AgentDefinition.memory` (a selector field for agent-specific memory scopes — `"user"` / `"project"` / `"local"`) and to `memoryFiles` keys in CLI response payloads (read-only consumer of CLI metadata, not an activator).
- `claude_agent_sdk/types.py:42` — `exclude_dynamic_sections` docstring mentions auto-memory only as a per-user dynamic section that the CLI may strip for prompt-cache friendliness. The SDK does not enable, write, or read auto-memory itself.

**Empirical evidence:**
- The `claude` CLI subprocess started by `ClaudeSDKClient` runs against `cwd=<wherever the SDK program runs>`. Whether the CLI itself touches its own auto-memory subsystem in this mode is a CLI-internal concern, not an SDK concern. From the SDK consumer's vantage point (claude-crew), the auto-memory subsystem is not a source of teammate context.

**Implication for SdkTeammate:**
Don't rely on auto-memory for cross-session teammate state. Persistent memory between *different* claude-crew sessions is a v2 concern (likely via the bus's transcript log + an explicit re-seed prompt). Within a session, the SDK's automatic conversation history (Section 1) is sufficient.

---

### Update 2026-04-25 — direct probe of the path

After Feature #3a shipped, we ran `scripts/auto_memory_probe.py` to
close the loop on whether top-level teammates can use the same path
Claude Code uses, even if the SDK doesn't activate the subsystem
itself. Findings:

- **Directory does not auto-populate.** Before the probe, the path
  `~/.claude/projects/-home-jerome-dev-claude-crew/memory/` did not
  exist. The SDK-spawned teammate's `claude` CLI subprocess does not
  create it. Confirms the source-read finding above.
- **But the path is fully writable from the teammate.** The teammate
  ran `mkdir -p ...memory/`, appended a sentinel string to
  `MEMORY.md`, and re-read the file successfully. The sentinel landed
  on disk; local-process verification confirmed it from the test
  harness side.
- **Implication:** cross-session teammate memory is a small
  capability lift, not the v2 architectural rebuild we'd assumed. We
  can have teammates read and write their own `MEMORY.md` at the
  Claude-Code-conventional path with explicit instructions in their
  system prompt (or a thin wrapper that loads and re-seeds the
  context on spawn). Same primitive Claude Code uses; we just have to
  invoke it ourselves rather than expect the SDK to.
- **Open design choice (not yet a feature):** if we ship this, do we
  share the auto-memory directory with Claude Code (so a teammate
  spawned in `~/dev/claude-crew` writes to the same `MEMORY.md` that
  Kael writes to from the Claude Code session) or namespace it by
  teammate identity (so each role has its own memory file)? Probably
  the latter for the default pack, optional sharing for custom
  teammates. Real decision when we wire it.

---

## Summary table

| Question | Answer | Mechanism |
|---|---|---|
| Within-session persistence? | Yes, automatic | Subprocess + `session_id` |
| `~/.claude/CLAUDE.md` loaded? | Only with `setting_sources=["user", ...]` | CLI flag |
| `<cwd>/CLAUDE.md` loaded? | Only with `setting_sources=[..., "project"]` | CLI flag |
| Auto-memory subsystem active? | No | SDK does not touch `memory/` paths |
