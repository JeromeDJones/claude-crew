# Debrief: multi-scope-agent-memory — implementor (task: sdk-teammate-multi-scope-injection)

Verdict: PASS

## Transferable lesson

When replacing a "not-yet-supported" WARN branch with real behavior, the existing tests that assert the old warning become the first casualty — and that's intentional signal, not collateral damage. The pattern: a stub WARN exists precisely because the feature was deferred; when you implement the feature, the test must flip from "assert WARN fires" to "assert WARN does not fire + assert new behavior." In this feature, `test_memory_warns_and_no_options_key` was testing the absence of support, so converting it to `test_memory_project_no_options_key_no_unsupported_warn` was the correct move, not a workaround. The deeper lesson for seam integration: always distinguish tests that assert *current behavior* from tests that assert *invariants* — invariants survive the flip (no `memory` field on `ClaudeAgentOptions`), while current-behavior tests need to be replaced by tests for the new behavior. The F3b shared-pack invariant (`self._agents = {**self._agents, role: patched_def}` rather than in-place mutation) was the load-bearing constraint that made `ensure_write_tool` composable — next time I see a shared mutable registry, I'll reach for the copy-on-write dict pattern before considering any other approach.
