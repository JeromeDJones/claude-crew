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
- [ ] **SC-8: Live verification — Write blocked.** A teammate explicitly told to write to `~/.claude/projects/<cwd>/memory/test.md` is blocked; the file is not created; the response contains the redirection message.
- [ ] **SC-8b: Live verification — Edit blocked.** A teammate explicitly told to edit `~/.claude/projects/<cwd>/memory/MEMORY.md` is blocked; the file content is unchanged; the response contains the redirection message.
- [ ] **SC-9: Symlink bypass closed.** A write to a path that resolves into the protected zone is blocked, regardless of which direction the symlink points (caller-supplied path is a symlink into the zone, OR a path within the zone is itself a symlink to a safe location). Both directions tested.

### Questions

- [x] **Should reads be guarded too?** No (v1). An agent reading the lead's project memory (e.g., to look up a referenced detail file from the auto-loaded index) is legitimate — it's information access, not pollution. Block-on-write is the asymmetric protection that matters.
- [x] **What about Bash with `cp`/`echo > file`/etc.?** Out of scope (v1). The guard covers Write and Edit — the canonical file-modification surface. Bash-based writes bypass the guard. If teammates start using Bash to circumvent, revisit. (Most SDK teammates don't have Bash anyway.)
- [x] **What about the MCP filesystem server, if loaded?** Out of scope (v1). The guard is a hook on the SDK's tool dispatch path. MCP-mediated writes would need their own guard. Defer until we see a concrete use case.
- [x] **What about teammates spawned with `cwd=` overrides into a different project?** Out of scope (v1). The guard protects the SERVER process's encoded cwd, not whatever cwd the teammate was spawned with. A teammate spawned with `cwd=/other-project` could still write to `/other-project`'s project memory if it knew the path. Accepted limitation; document it in the constraints. In practice all teammates run in the server's project.
- [x] **NotebookEdit and other write-capable tools?** Out of scope (v1). Cover only Write and Edit by name. Add others as we encounter them.

### Constraints & Dependencies

- Pure internal change — no MCP tool surface changes, no API changes.
- Implementation lives in `claude_crew/sdk_teammate.py` — extends the existing PreToolUse hook (already wired at `_on_pre_tool_use` and registered via `HookMatcher` at line ~975).
- Hook return shape: per `claude-agent-sdk` convention, returning `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..."}}` from a PreToolUse hook causes the SDK to deny the tool call. (Verify exact shape against the SDK version pinned in `pyproject.toml` before implementation.)
- Must not break the 889-test suite. Existing PreToolUse tests will need updating only if they exercise Write/Edit on disallowed paths (unlikely — most tests target other tool names).
- Must not interfere with the live SC-5 from the parent feature: a teammate writing to its OWN `~/.claude/agent-memory/<role>/` must succeed. Re-running that test is the regression check.

**Path-matching algorithm (revised after Sentinel review — H-2 fix):**

The naive `"projects" in path.parts and "memory" in path.parts` check has TWO bugs:
1. False positive on roles like `~/.claude/agent-memory/projects/memory/notes.md` (no project segment after `projects`)
2. Symlink-out bypass: if `~/.claude/projects/x/memory/` is itself a symlink to `/tmp/safe`, `Path(...).resolve()` returns `/tmp/safe/...` and the guard passes silently.

Correct algorithm — **dual-check both expanded and resolved paths**:

```python
def _is_lead_project_memory(write_path: str) -> bool:
    expanded = Path(write_path).expanduser()
    protected_root = Path.home() / ".claude" / "projects"
    candidates = [expanded]
    try:
        candidates.append(expanded.resolve(strict=False))
    except (OSError, RuntimeError):
        pass  # resolve() can fail on broken symlinks; expanded check still applies
    for candidate in candidates:
        try:
            rel = candidate.relative_to(protected_root)
        except ValueError:
            continue  # not under ~/.claude/projects/ at all
        # rel.parts must be: (<project_slug>, "memory", ...)
        if len(rel.parts) >= 2 and rel.parts[1] == "memory":
            return True
    return False
```

This:
- Uses `relative_to(protected_root)` instead of `"projects" in parts` — eliminates the false positive
- Checks `rel.parts[1] == "memory"` (position-specific) instead of `"memory" in parts` — defends against `~/.claude/projects/foo/bar/memory/` non-matches
- Checks BOTH expanded and resolved variants — defends against symlink-out (in addition to the standard symlink-in defense from `resolve()`)

**Guarded tool names:** `Write`, `Edit`. Apply guard when `tool_name in ("Write", "Edit")` and `tool_input.get("file_path")` matches the algorithm above.

**Path exposure note (M-6):** The guard's error message and the parent feature's disambiguation note both expose `~/.claude/projects/<encoded-cwd>/memory/` to the teammate. The teammate could derive this anyway via `os.getcwd()` + the encoding rule, so the exposure isn't novel. Accept.

**Gate**: Questions resolved, success criteria measurable, scope tightened to write-only, algorithm corrected against symlink/parts-check bypasses, Edit tool explicitly in scope.

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
