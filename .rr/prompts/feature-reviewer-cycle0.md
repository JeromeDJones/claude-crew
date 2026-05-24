## Task

Review the cross-slice synthesis for spec `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/specs/agent-pack-refresh.md`. Three checks: cross-
slice integration coherence, holistic spec satisfaction, cracks-fell-through
detection. Your final-turn text is the report. The coordinator persists it
to:
`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-feature-review-0.md`

Use the `review-feature` skill for the verification checklist, severity
tiers, tag vocabulary, and verdict rule.

## Inputs

Spec: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/specs/agent-pack-refresh.md`
Breakout: `/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/specs/agent-pack-refresh.md`

All build reports and slice-review reports for this feature live under
`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/`. Read them — the slice-review reports'
Info-tier `slice.review-process.cross-slice-observation` findings are leads
for your work.

## Branch Diff

```
=== Code diff summary (master...HEAD, code only) ===
 claude_crew/factories.py   | 191 ++++++++++++++++-
 claude_crew/server.py      |  32 +++
 tests/test_pack_refresh.py | 502 +++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 714 insertions(+), 11 deletions(-)

=== Full diff: claude_crew/server.py (the new MCP tool surface) ===
diff --git a/claude_crew/server.py b/claude_crew/server.py
index 2dee754..eeb81fe 100644
--- a/claude_crew/server.py
+++ b/claude_crew/server.py
@@ -528,6 +528,38 @@ def make_server(
             "project_root": str(_project_root),
         }
 
+    @mcp.tool()
+    async def refresh_agents() -> dict[str, Any]:
+        """Reload agent definitions from disk and swap the in-memory pack.
+
+        future-spawns-only: already-running teammates keep the AgentDefinition
+        snapshot they were spawned on. Refresh only affects teammates spawned
+        AFTER this call returns — in-flight or previously-spawned teammates are
+        not mutated.
+
+        Returns a RefreshResult dict with:
+          ok:       True if the rebuild succeeded; False if build_merged_pack raised.
+          error:    Exception repr when ok=False; None otherwise.
+          counts:   Post-refresh pack counts per layer (default/plugin/user/project/total).
+          diff:     {added, removed, changed} role keys vs the prior pack.
+          warnings: WARN/INFO records captured during this refresh pass.
+          note:     Human-readable future-spawns-only reminder.
+        """
+        refresh_fn = getattr(factory, "refresh_pack", None)
+        if refresh_fn is None:
+            # Fallback no-op (should not happen in practice; stub and sdk modes
+            # both attach refresh_pack).
+            from claude_crew.factories import _REFRESH_NOTE
+            return {
+                "ok": True,
+                "error": None,
+                "counts": {"default": 0, "plugin": 0, "user": 0, "project": 0, "total": 0},
+                "diff": {"added": [], "removed": [], "changed": []},
+                "warnings": [],
+                "note": _REFRESH_NOTE,
+            }
+        return refresh_fn()
+
     @mcp.tool()
     async def surface_document(path: str, title: str) -> dict[str, Any]:
         """Surface a markdown document to Mission Control for operator review.

(factories.py +191 and tests/test_pack_refresh.py +502 are large — READ them directly in the worktree for full detail.)
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

`/home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh`

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
