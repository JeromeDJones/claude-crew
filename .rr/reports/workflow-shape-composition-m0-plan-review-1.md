# Plan Review: workflow-shape-composition-m0

**Verdict:** PASS

**Cycle:** 1 (prior report: `workflow-shape-composition-m0-plan-review-0.md`)

## Summary

The cycle-0 blockers are resolved, and I verified the new mechanism claim against
the actual code rather than taking it on faith. The spec remains clear,
well-scoped, and decomposed into single-surface tasks with a clean 1:1
acceptance-test cover (now 14 ATs) and a sound dependency DAG. No findings recur;
no new blocking issues were introduced. This passes.

## Prior-Finding Disposition

**H1 (cycle-0, High) — RESOLVED.** The pre-flight role resolution now names a real,
verified mechanism *and* gains a dedicated all-or-nothing AT:
- **Mechanism:** `factory.known_roles` = `lambda: tuple(holder.pack.keys())`, added in
  the same idiom as `factory.startup_diagnostics`. I confirmed that idiom exists —
  `factory.startup_diagnostics` is attached at factories.py:549, `factory._holder` at
  551, and `holder.pack` is read live (426/536). `instantiate_shape` reads it via
  `getattr(factory, "known_roles", None)`; absent on the stub default → pre-flight
  skipped (preserves today's spawn-time resolution and AT#8's happy path).
- **Resolution semantics:** the spec's "exact match in `known` OR exactly one key
  ending `:role`" faithfully mirrors `factories._resolve_role` (380–414): exact match,
  else a single `*:requested` candidate promotes, else fall through (unresolvable).
  Multiple candidates correctly count as unresolvable. Accurate.
- **Dedicated AT:** AT#14 — an *approved* 2-node shape with one `role="ghost-role"`
  absent from an injected `known_roles` set → `{ok:False}` naming the unresolved role,
  `list_crew` shows **zero** teammates ("the resolvable node is not spawned either").
  This is exactly the missing all-or-nothing test cycle-0 flagged. Claimed by
  `shape-mcp-tools`, whose touch set now includes `claude_crew/factories.py`.

**M1 (cycle-0, Medium) — RESOLVED via clean scope-out.** `edited_shape` is fully
removed from the contract and assumptions, not just deferred in prose:
- `resolve_proposal(shape_id, decision)` — no `edited_shape` param (contract line 132).
- POST body is `{"decision"}` only (line 163); the call-site survey (60–67) dropped it.
- A new Design Decision documents approve-or-decline-only with the decline+re-propose
  tweak path; Out of Scope lists "Operator in-gate shape editing" first; Assumptions
  now state the `{decision}`-only body. No untested in-scope branch remains.

**M2 (cycle-0, Medium, decomposition) — ADDRESSED.** The heavy dashboard task is split
at the `/api/state` seam: `dashboard-shape-state-route` (ATs 11/12 — state emission +
POST route + proxy + httpx tests) and `dashboard-shape-render` (AT 13 — mermaid panel
+ XSS, Playwright). Disjoint files; render `dependsOn` state-route because it consumes
the emitted `mermaid` field. Both are now single-surface.

**L1 / L2 — both addressed.** Tuple→list conversion (`list(node.extra_tools or ()) or
None`) is in the contract, an Edge Case, and a Design Note. `phases` exemption from the
unknown-key guard is stated in the schema docstring, an Edge Case, the AT#4 note, and a
Design Note.

## Findings

### High
None.

### Medium
None.

### Low

**L3 (new, non-blocking) — Promotion logic is reimplemented, not shared, risking drift.**
`factories._resolve_role` is a nested closure inside the sdk-factory builder (line 380),
not module-level, so `instantiate_shape` cannot call it directly and must reimplement
the bare→`*:role` promotion against `known_roles()` output. The spec describes the rule
inline and AT#14 anchors it, so it is testable — but two copies of the promotion rule
can drift if `_resolve_role` changes later. Optional hardening: hoist `_resolve_role`
to module scope (or expose a `factory.resolve_role` accessor) so the pre-flight and the
spawn path share one implementation. Not a blocker — the behavior is specified and tested.

## Acceptance-Test Coverage (Breakout)

| AT | Task | Status |
|----|------|--------|
| 1,2,3,4 | shape-schema-parser | claimed once |
| 5,6,7 | broker-proposals-topology | claimed once |
| 8,9,10,14 | shape-mcp-tools | claimed once |
| 11,12 | dashboard-shape-state-route | claimed once |
| 13 | dashboard-shape-render | claimed once |

All 14 ATs covered exactly once — no duplicates, no unclaimed ATs. Dependency DAG:
schema → broker → {mcp-tools, state-route}; state-route → render. mcp-tools and
state-route are correctly independent (parallel-safe, disjoint files:
`server.py`+`factories.py`+`test_shape_gate.py` vs.
`ui_server.py`+`test_shape_dashboard.py`); render depends on state-route only because it
consumes the `mermaid` field. No spurious edges. No mega-task. `shape_to_mermaid` is
authored under shape-schema-parser and exercised by AT#1; the graphical render is AT#13
under dashboard-shape-render — coherent split of the mermaid concern.

## Test Command

`uv run pytest` — runnable as written; the full-suite choice is correctly justified
(cross-cutting `BrokerSnapshot` fields + tool-surface growth). AT#13's Playwright prereq
(`uv run playwright install chromium`) is called out; the Validation block's per-suite
list now includes `tests/test_shape_render.py`, consistent with the new task split.
Per-task `testCommand`s are present and consistent.

## Verification Notes

- `factories.py`: `factory.startup_diagnostics` accessor idiom (549), `factory._holder`
  (551), live `holder.pack` reads (426/536) — confirms the `factory.known_roles`
  addition is the same low-risk pattern.
- `factories._resolve_role` (380–414): exact-match / single-`:role`-candidate promotion
  confirmed; the spec's pre-flight matching mirrors it accurately.
- `server.spawn_teammate` still does not pre-validate roles — confirming the spec's
  rationale that a new enumeration API (`known_roles`) is genuinely needed.
- Carry-forward from cycle 0 (mermaid reuse, `_proxy_artifact`/`_PATH_PARAM_RE`/
  `_own_crew_id`, `_lead_message_condition`, `spawn_teammate` signature) remains valid;
  the revision did not disturb those seams.
