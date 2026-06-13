# Slice Review: m2-edge-routing task=scoped-send-teammate (cycle 0)

## Verdict summary
**REQUEST-CHANGES.** The owned acceptance tests (8, 9, 10) are green and non-regression holds, but this slice's **central teammate-facing deliverable — the in-process SDK MCP `send_to` tool whose handler calls `broker.send_scoped` — was not built**. Worse, a changed file (`teammate_prompt.py`) now ships a system-prompt section instructing teammates to "message via `send_to`", advertising a tool that does not exist. This is the exact "shipped green, broke on first real use" pattern the repo's own CLAUDE.md documents twice. One High finding flips the verdict.

## Check 1 — Slice adherence (AT#8, AT#9, AT#10)

| AT | Requirement | Implementation | Test | Status |
|----|-------------|----------------|------|--------|
| #8 neighbor injection | assembled `system_prompt_override` names out-edge (role, mode) + in-edge (role, mode) | `_build_neighbors_section` + `neighbors=` threaded build_teammate_prompt → broker.spawn_teammate → factories → SdkTeammate; server.instantiate_shape computes per-node adjacency from `shape.edges` | `TestNeighborInjection` (7 tests) | ✓ |
| #9 scoped authorize/reject | `broker.send_scoped(a,"b")` delivers; `(a,"c")` raises `UnauthorizedEdgeError` | uses `broker.send_scoped` (built in task 0) | `TestScopedSendAuthorize` (6 tests) | ✓ |
| #10 ping-pong off lead | `a→b→a→b` below budget → none reach LEAD; all in broker log | uses `broker.send_scoped` direct-edge routing | `TestDirectPingPong` (4 tests) | ✓ (broker level) |

**The owned ATs pass — but at the broker/prompt level only.** None exercises the in-process MCP tool, because the spec deliberately defers the *live-SDK test* of that tool to `CLAUDE_CREW_LIVE_TESTS` (Out of Scope). That deferral is for the **test**, not the **deliverable**: the spec's Out-of-Scope line itself presupposes the tool exists ("the broker contract is covered in stub mode; live verification follows…"). The green ATs are the necessary-not-sufficient subset; the slice still owes the tool.

## Check 2 — Non-regression
- `uv run pytest tests/test_scoped_send.py` + siblings → **53 passed** (independent re-run).
- Corroborates coordinator ground-truth (scoped 17, siblings 36, full suite **1515 / 0 failed**). Parallel isolation held (no ui-file touches).

## Check 3 — Code-quality smoke (changed files) — **High finding**

**The in-process SDK MCP `send_to` tool is absent.** Verified three ways:
1. `sdk_teammate.py` diff is only the `neighbors=` param + its pass-through to `build_teammate_prompt` (+3/−1). No `create_sdk_mcp_server`, no `@tool`, no `send_to` handler.
2. Repo-wide, `send_scoped` has **zero production callers** — `grep -rn send_scoped claude_crew/` returns only the *definition* in `broker.py`. Nothing in the shipped code ever invokes it; only tests do.
3. The build report's own `sdk_teammate.py` summary lists only the neighbors-param threading — no tool.

**Consequence — incoherent shipped behavior in a changed file:** `teammate_prompt.py`'s new `_build_neighbors_section` emits `Out-edges (teammates you may message via `send_to`):` — a real SdkTeammate receives this instruction but has **no `send_to` tool to call**. The M2 user-visible promise ("two teammates talk to each other without the lead in the loop") is non-functional for real teammates; only the broker plumbing (task 0) and the advertising text ship. The moat invariant ("every cross-teammate message routes through `broker.send_scoped`, no off-broker channel") cannot be confirmed — there is no teammate entry point to the broker at all.

This deliverable is assigned to *this* task by name (breakout: "…SdkTeammate (…including an in-process SDK MCP `send_to` tool whose handler calls `broker.send_scoped`)") and to no other task — so if this slice doesn't build it, no slice does. That places it squarely within slice adherence.

## Invariant-1 adjudication (`factories.py` outside declared `taskTouches`)
**`breakout.scope.under-declared` (Info — legitimate, NOT creep).** The `neighbors=` kwarg must thread through `stub_factory`, `sdk_factory`, and `default_factory`'s inner closure. Minimal, signature-only edits; stub ignores it. Same class as task 0's `test_broker.py` under-declaration. Does not affect the verdict.

## Findings

| Sev | Tag | Finding |
|-----|-----|---------|
| **High** | `slice.incomplete` | The in-process SDK MCP `send_to` tool (handler → `broker.send_scoped`) — this task's explicitly-assigned, central teammate-facing deliverable — was not implemented. `teammate_prompt.py` ships a prompt advertising `send_to` to teammates that have no such tool, leaving M2 peer messaging non-functional for real SdkTeammates. **Required fix:** register an in-process SDK MCP `send_to(recipient, payload)` tool in `SdkTeammate` whose handler calls `self._broker.send_scoped(self.id, recipient, payload)`, and grant it to the teammate's tool surface. Live verification stays `CLAUDE_CREW_LIVE_TESTS`-gated per Out-of-Scope; a stub-level assertion that the tool is registered/wired would close the green-suite gap. |
| Info | `breakout.scope.under-declared` | `factories.py` edited to thread `neighbors=` through the factory chain — necessary, minimal, no behavior change. |

The neighbor-injection (AT#8), authorization (AT#9), and ping-pong (AT#10) work that *is* present is correct and well-tested. The verdict is driven solely by the missing `send_to` tool.
