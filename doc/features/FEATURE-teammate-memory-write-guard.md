# Feature: Teammate Memory Write Guard

**Status**: Planning
**Created**: 2026-05-04
**Depends on**: `FEATURE-teammate-memory-persistence.md` (merged)

---

## Phase 1: Research & Requirements

### Problem Statement

The teammate memory persistence feature established that SDK-spawned teammates should use `~/.claude/agent-memory/<role>/` for their memory — same convention the CLI uses for subagents. The injected scaffolding instructs them clearly, including a disambiguation note about the lead's project-scoped memory (`~/.claude/projects/<encoded-cwd>/memory/`) which auto-loads as a `system-reminder`.

**The risk:** an SDK teammate sees both memory contexts in its system prompt. The lead's project memory is salient (system-reminder framing) and contains rich content (Kael's accumulated notes). A confused or mis-prompted teammate could write into the lead's project memory directory, polluting Kael's index and detail files. The damage isn't catastrophic — files can be removed — but the lead's memory becomes the shared accumulator everyone touches, which violates its purpose (it's *the lead's* curated memory).

The injected disambiguation note tells the agent not to do this. Instructions are necessary but not sufficient — they're a soft guard. We need a hard guard: a write boundary that blocks SDK teammates from writing under `~/.claude/projects/*/memory/**` regardless of intent.

### Success Criteria

- [ ] **SC-1: Write to lead project memory blocked.** When an SDK teammate attempts to use the Write tool with a path under `~/.claude/projects/*/memory/**`, the call is blocked before reaching the filesystem. The teammate receives a clear error message explaining where it should write instead.
- [ ] **SC-2: Write to own agent memory unaffected.** Writes to `~/.claude/agent-memory/<role>/**` proceed normally. The guard does not interfere with legitimate memory persistence.
- [ ] **SC-3: Writes outside memory paths unaffected.** Writes to project source files, tmp paths, or anywhere not matching the lead-project-memory pattern proceed normally. The guard is narrowly scoped.
- [ ] **SC-4: Edit tool also guarded.** The Edit tool (which can rewrite or replace file content) is subject to the same guard for the same paths. Otherwise the guard is trivially bypassed.
- [ ] **SC-5: Block message names the right destination for the role.** The error tells the teammate exactly where its memory should go: `~/.claude/agent-memory/<role>/`. Generic "blocked" without redirection is unhelpful.
- [ ] **SC-6: Guard applies to ALL SDK teammates, not just `memory: user` ones.** A teammate without `memory: user` declared still cannot write to the lead's project memory. The guard is a safety boundary, not a memory-feature opt-in.
- [ ] **SC-7: No regression on existing 889-test suite.** Specifically, no false positives on existing Write/Edit tests.
- [ ] **SC-8: Live verification.** A teammate explicitly told to write to `~/.claude/projects/<cwd>/memory/test.md` is blocked; the file is not created; the response contains the redirection message.

### Questions

- [ ] **Should reads be guarded too?** Decision deferred from parent feature: reads are NOT guarded in v1. An agent reading the lead's project memory (e.g., to look up a referenced detail file from the auto-loaded index) is legitimate-ish — it's information access, not pollution. Block-on-write is the asymmetric protection that matters.
- [ ] **What about Bash with `cp`/`echo > file`/etc.?** Out of scope for v1. The guard covers Write and Edit tools — the canonical file-modification surface. Bash-based writes bypass the guard. If teammates start using Bash to circumvent, revisit. (Most SDK teammates don't have Bash anyway.)
- [ ] **What about the MCP filesystem server, if loaded?** Out of scope for v1. The guard is a hook on the SDK's tool dispatch path. MCP-mediated writes would need their own guard. Defer until we see a concrete use case.

### Constraints & Dependencies

- Pure internal change — no MCP tool surface changes, no API changes.
- Implementation lives in `claude_crew/sdk_teammate.py` — extends the existing PreToolUse hook (already wired at `_on_pre_tool_use` and registered via `HookMatcher` at line ~975).
- Hook return shape: per `claude-agent-sdk` convention, returning `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}` from a PreToolUse hook causes the SDK to deny the tool call. (Verify exact shape against the SDK version pinned in `pyproject.toml` before implementation.)
- Must not break the 889-test suite. Existing PreToolUse tests will need updating only if they exercise Write/Edit on disallowed paths (unlikely — most tests target other tool names).
- Must not interfere with the live SC-5 from the parent feature: a teammate writing to its OWN `~/.claude/agent-memory/<role>/` must succeed. Re-running that test is the regression check.

**Path-matching convention:**
- Block: any `Path(write_path).expanduser().resolve()` whose parts include both `projects` and `memory` AND is under `~/.claude/projects/`. Specifically: matches `~/.claude/projects/*/memory/**`.
- Allow: anything else, including `~/.claude/agent-memory/**` (the right place) and all unrelated paths.
- Use `Path.resolve()` to defeat symlink/relative-path bypasses.

**Gate**: Questions resolved, success criteria measurable, scope tightened to write-only.

---

## Phase 2: Design & Specification

*Pending Phase 1 gate.*

---

## Phase 3: Task Breakdown

*Pending Phase 2 gate.*

---

## Phase 4: Implementation

*Pending Phase 3 gate.*

---

## Phase 5: Completion

*Pending Phase 4 gate.*
