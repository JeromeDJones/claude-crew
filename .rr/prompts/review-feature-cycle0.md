## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/specs/multi-scope-agent-memory.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/multi-scope-agent-memory-feature-review-0.md`

Use the `review-feature` skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/specs/multi-scope-agent-memory.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/specs/multi-scope-agent-memory.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
diff --git a/claude_crew/sdk_teammate.py b/claude_crew/sdk_teammate.py
index 22db346..06f5c63 100644
--- a/claude_crew/sdk_teammate.py
+++ b/claude_crew/sdk_teammate.py
@@ -497,13 +497,22 @@ class SdkTeammate(Teammate):
         role_def = self._agents.get(role)
         role_memory = getattr(role_def, "memory", None)
 
-        # Warn once for unsupported memory values; suppress for "user" (injection handles it).
-        if role_memory in ("project", "local"):
-            logger.warning(
-                "teammate=%s role=%s pack declares memory=%r; only 'user' is "
-                "supported in v1 — no injection performed",
-                self.id, role, role_memory,
-            )
+        # Memory scope injection for user / project / local scopes.
+        # ensure_write_tool fires regardless of pack_bodies availability so that
+        # self._agents[role].tools always reflects the Write capability needed for
+        # memory persistence. build_memory_section is deferred into the pack-body
+        # block below (no I/O when a system_prompt override is active).
+        _memory_project_root: Path | None = None
+        if role_memory in ("user", "project", "local"):
+            from claude_crew.teammate_memory import build_memory_section, ensure_write_tool
+            if role_memory in ("project", "local"):
+                _memory_project_root = (
+                    Path(cwd).resolve() if cwd else Path.cwd()
+                )
+            patched_def = ensure_write_tool(role_def)
+            if patched_def is not role_def:
+                self._agents = {**self._agents, role: patched_def}
+                role_def = patched_def
 
         if system_prompt is not None:
             self._system_prompt = system_prompt  # explicit override wins (edge case 2)
@@ -512,14 +521,17 @@ class SdkTeammate(Teammate):
             if _body is not None:
                 # Memory section computed inside else — skip I/O when override is active.
                 _memory_section = None
-                if role_memory == "user":
-                    from claude_crew.teammate_memory import build_memory_section
+                if role_memory in ("user", "project", "local"):
                     try:
                         _memory_section = build_memory_section(
-                            role, getattr(role_def, "tools", None)
+                            role,
+                            getattr(role_def, "tools", None),
+                            scope=role_memory,
+                            project_root=_memory_project_root,
                         )
                         logger.debug(
-                            "teammate=%s role=%s memory section injected", self.id, role
+                            "teammate=%s role=%s memory section injected (scope=%s)",
+                            self.id, role, role_memory,
                         )
                     except ValueError:
                         logger.warning(
diff --git a/claude_crew/teammate_memory.py b/claude_crew/teammate_memory.py
index 769be4f..4aff345 100644
--- a/claude_crew/teammate_memory.py
+++ b/claude_crew/teammate_memory.py
@@ -20,11 +20,16 @@ path hints and observing where it wrote — `~/.claude/agent-memory/sentinel/`.
 
 from __future__ import annotations
 
+import dataclasses
 import re
 from pathlib import Path
+from typing import TYPE_CHECKING
 
 from claude_crew.teammate_prompt import SENTINEL_MEMORY
 
+if TYPE_CHECKING:
+    from claude_agent_sdk.types import AgentDefinition
+
 
 _MAX_INDEX_LINES = 200  # mirror the CLI's MEMORY.md auto-load truncation
 
@@ -45,15 +50,45 @@ def _sanitize_role(role: str) -> str:
     return role
 
 
-def memory_dir(role: str) -> Path:
-    """Return the role's memory directory. Pure — no I/O."""
-    safe = _sanitize_role(role)
-    return Path.home() / ".claude" / "agent-memory" / safe
+def memory_dir(
+    role: str,
+    scope: str = "user",
+    project_root: Path | None = None,
+) -> Path:
+    """Return the role's memory directory for the given scope. Pure — no I/O.
 
+    user    -> Path.home() / ".claude" / "agent-memory" / <role>
+    project -> project_root / ".claude" / "agent-memory" / <role>
+    local   -> project_root / ".claude" / "agent-memory.local" / <role>
 
-def memory_index_path(role: str) -> Path:
+    Raises ValueError if scope is "project" or "local" and project_root is None.
+    """
+    safe = _sanitize_role(role)
+    if scope == "user":
+        return Path.home() / ".claude" / "agent-memory" / safe
+    elif scope == "project":
+        if project_root is None:
+            raise ValueError(
+                "project_root is required when scope is 'project'"
+            )
+        return Path(project_root) / ".claude" / "agent-memory" / safe
+    elif scope == "local":
+        if project_root is None:
+            raise ValueError(
+                "project_root is required when scope is 'local'"
+            )
+        return Path(project_root) / ".claude" / "agent-memory.local" / safe
+    else:
+        raise ValueError(f"Unknown scope: {scope!r}")
+
+
+def memory_index_path(
+    role: str,
+    scope: str = "user",
+    project_root: Path | None = None,
+) -> Path:
     """Return the role's MEMORY.md path. Pure — no I/O."""
-    return memory_dir(role) / "MEMORY.md"
+    return memory_dir(role, scope=scope, project_root=project_root) / "MEMORY.md"
 
 
 # ---------------------------------------------------------------------------
@@ -110,19 +145,39 @@ def write_guard_deny_message(role: str, attempted_path: str) -> str:
 # ---------------------------------------------------------------------------
 
 
-def build_memory_section(role: str, tools: tuple[str, ...] | None) -> str:
+def build_memory_section(
+    role: str,
+    tools: tuple[str, ...] | None,
+    scope: str = "user",
+    project_root: Path | None = None,
+) -> str:
     """Build the memory addendum for a teammate's system prompt.
 
     Mirrors the scaffolding the CLI provides to subagents with `memory: user`:
     location guidance, save/skip rules, save format, and the first 200 lines
     of the role's MEMORY.md index. Never raises — I/O errors surface in the
     injected text.
+
+    Picks scope-specific guidance text based on the `scope` kwarg.
     """
     has_write = "Write" in (tools or ())
-    directory = memory_dir(role)
-    index_path = memory_index_path(role)
+    directory = memory_dir(role, scope=scope, project_root=project_root)
+    index_path = memory_index_path(role, scope=scope, project_root=project_root)
     index_block = _read_index(index_path)
 
+    if scope == "project":
+        header = _INSTRUCTIONS_HEADER_PROJECT
+        what_to_save = _INSTRUCTIONS_WHAT_TO_SAVE_PROJECT
+        what_not_to_save = _INSTRUCTIONS_WHAT_NOT_TO_SAVE_PROJECT
+    elif scope == "local":
+        header = _INSTRUCTIONS_HEADER_LOCAL
+        what_to_save = _INSTRUCTIONS_WHAT_TO_SAVE_LOCAL
+        what_not_to_save = _INSTRUCTIONS_WHAT_NOT_TO_SAVE_LOCAL
+    else:
+        header = _INSTRUCTIONS_HEADER_USER
+        what_to_save = _INSTRUCTIONS_WHAT_TO_SAVE_USER
+        what_not_to_save = _INSTRUCTIONS_WHAT_NOT_TO_SAVE_USER
+
     persistence_note = "" if has_write else (
         "\n\n**Note:** The Write tool is not in your tool list — you cannot "
         "persist new memories from this session. You can still read prior "
@@ -132,9 +187,9 @@ def build_memory_section(role: str, tools: tuple[str, ...] | None) -> str:
 
     return (
         f"{SENTINEL_MEMORY}\n\n"
-        f"{_INSTRUCTIONS_HEADER.format(role=role, directory=directory, index_path=index_path)}\n\n"
-        f"{_INSTRUCTIONS_WHAT_TO_SAVE}\n\n"
-        f"{_INSTRUCTIONS_WHAT_NOT_TO_SAVE}\n\n"
+        f"{header.format(role=role, directory=directory, index_path=index_path)}\n\n"
+        f"{what_to_save}\n\n"
+        f"{what_not_to_save}\n\n"
         f"{_INSTRUCTIONS_WHEN_NOT_TO_SAVE}\n\n"
         f"{_INSTRUCTIONS_HOW_TO_SAVE.format(directory=directory, index_path=index_path)}\n\n"
         f"### Your prior memories (from MEMORY.md)\n\n"
@@ -143,6 +198,25 @@ def build_memory_section(role: str, tools: tuple[str, ...] | None) -> str:
     )
 
 
+# ---------------------------------------------------------------------------
+# Write-tool helper
+# ---------------------------------------------------------------------------
+
+
+def ensure_write_tool(agent_def: "AgentDefinition") -> "AgentDefinition":
+    """Return agent_def unchanged if Write is in tools; else a dataclasses.replace
+    copy with Write appended. Never mutates the input.
+
+    Handles tools=None (pack omitted tools:) by treating it as empty and
+    returning a copy with Write as the sole tool.
... and 476 more lines truncated — read claude_crew/teammate_memory.py, claude_crew/sdk_teammate.py, tests/test_teammate_memory.py, tests/test_sdk_teammate.py in the worktree for full detail.
```

This is the synthesis surface — every file the assembled feature touches.

## Cycle

Cycle: 0
Prior report: 

On cycle ≥ 1, read the prior feature-review report first. Surface findings
that recurred — unresolved issues carry forward and count against PASS.

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your output; align your feature-review with the
architecture it describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/multi-scope-agent-memory`

Change to this directory before all file operations.

## Instructions

- Verdict must be **PASS** or **REQUEST-CHANGES**.
- Three checks only: integration coherence, holistic spec satisfaction,
  cracks. Per-slice quality issues belong to slice-review (already done).
- Run the spec's test command at least once via `Bash` as the feature-level
  non-regression check.
- On cycle ≥ 1: compare findings against the prior report.
- Final line of your response must be exactly:
  `RR-VERDICT: PASS|REQUEST-CHANGES <slug> <cycle> <feature-review-report-path>`
