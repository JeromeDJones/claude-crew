# Debrief: multi-scope-agent-memory — implementor (task: write-guard-noncollision-regression)

Verdict: PASS

## Transferable lesson

**Regression-pin tests are most durable when they derive their inputs from the production helper they're guarding, not from hardcoded path literals.** In this feature, `memory_dir(role, scope=..., project_root=...)` is the canonical path authority — constructing test paths any other way would let the test pass even if the helper's output drifted, defeating the invariant pin entirely. The pattern: identify the one function that owns the path convention, use it to build both the "safe" inputs and the assertion subject, and name the test class explicitly for the invariant (`TestWriteGuardNoncollision`) so the keyword filter is self-documenting and grep-findable. Testing both the directory form and the file-under-directory form matters too — a guard that blocks the dir but passes a child file (or vice versa) is only half-correct. Next time I see a "document the non-collision" task, I'll ask: does the test call the production path-builder, or does it hardcode a string that could silently diverge?
