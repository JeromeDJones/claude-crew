# Build Report: graceful-termination-memory-flush task=doc-sync-wiring

**Tier:** trivial — coordinator-applied (no implementor/slice-reviewer spawn; feature-review is the backstop).
**Task index:** 5 · **Owns:** AT#15 · **implementationKind:** documentation

## Change
Fixed the false claim at `doc/sdk-teammate-wiring.md:109`. Removed "Auto-distilled at `kill_teammate`
exit" (behavior that did not exist) and replaced it with an accurate two-part description: (1) the
teammate writes its project memory file *itself* mid-session via the `Write` tool per the injected
`build_memory_section`/`ensure_write_tool` guidance (claude-crew does not parse the conversation),
and (2) the new graceful-termination flush — one final bounded (≤90s) turn before tombstone on an
explicit `kill_teammate` / `shutdown_all` of a healthy memory-bearing teammate, skipped for
memory-less teammates, `graceful=False` hard kills, and unexpected death.

## Test
`! grep -q "Auto-distilled at" doc/sdk-teammate-wiring.md` → PASS (stale phrase absent). AT#15 satisfied.

## Files changed
`git diff --name-status HEAD`:
```
M	doc/sdk-teammate-wiring.md
```

RR-VERDICT: PASS graceful-termination-memory-flush 0 /home/jerome/dev/claude-crew/.rr-worktrees/graceful-termination-memory-flush/.rr/reports/graceful-termination-memory-flush-task-doc-sync-wiring-build-0.md [coordinator-trivial]
