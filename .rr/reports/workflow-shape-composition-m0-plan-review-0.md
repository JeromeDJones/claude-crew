# Plan Review: workflow-shape-composition-m0

**Verdict:** REQUEST-CHANGES

**Cycle:** 0 (no prior report)

## Summary

This is a strong, well-grounded spec. The architecture survey is concrete and
verified-against-reality: the mermaid reuse claim (`mermaid.render()` +
`renderMermaidBlocks` + `securityLevel:'strict'` + DOMPurify with foreignObject
allowance), the `_proxy_artifact` / `_PATH_PARAM_RE` / `_own_crew_id`
multi-instance pattern, the `_lead_message_condition` long-poll precedent, and
the `broker.spawn_teammate(role, name, factory, model, …)` signature all exist
exactly as described. The acceptance-test → task mapping is a clean 1:1 cover of
ATs 1–13 with a correct dependency DAG and disjoint parallel-task file
footprints. Scope discipline is excellent (M0 records edge modes, enforces
nothing; deferrals to M1–M5 are explicit).

It does not pass because two **in-scope behaviors carry no acceptance test**, and
one of them ("all-or-nothing pre-flight role resolution") asserts a mechanism the
codebase does not currently expose — the existing spawn path does not pre-validate
roles. An autonomous implementor would hit this gap mid-build and have to invent
the resolution path with no test to anchor correctness. That is the
human-recovery-mid-build failure the bar exists to catch.

## Findings

### High

**H1 — The "pre-flight role resolution → all-or-nothing" behavior has no acceptance test, and its mechanism is unspecified against a codebase that does not pre-validate roles.**
`instantiate_shape`'s most safety-critical behavior — refuse the *entire*
instantiate if any node's `role` is unknown, spawning **zero** teammates — is
elevated to a Design Decision, an Edge Case ("A node's `role` does not resolve …
no teammate is spawned"), and the third row of the Validation Contracts table.
Yet none of AT#8/9/10 exercise it: AT#9 is *unapproved* instantiate, AT#10 is
*declined*. There is no "approved shape, one bad role → nothing spawned" test.
Separately, I verified `server.spawn_teammate` (server.py:138–269) calls
`broker.spawn_teammate` directly with **no** prior role-existence check — role
resolution happens inside the factory at spawn time. So "resolve every node.role
against the pack BEFORE spawning any" requires an API the tool closure does not
currently call. A mechanism does exist (`discover_dir(agents_dir).keys()`, as
`list_available_tools` at server.py:584 uses, or the factory's `refresh_pack`
pack), but the spec does not name it, and the all-or-nothing semantics are
untested. *Fix:* (a) add a numbered AT under `shape-mcp-tools` for "approved
shape with an unresolvable role → `{ok:False}`, `list_crew` shows zero teammates";
(b) name the pack-enumeration API the pre-flight check should consult.

### Medium

**M1 — The operator "tweak on approve" (`edited_shape`) path is in the contract and Assumptions but has no acceptance test.**
`resolve_proposal(…, edited_shape: Shape | None)`, the dashboard POST body field
`"shape"?: <edited dict>`, and Assumption #6 ("when present on approve, replaces
the proposal's shape … re-validated via `parse_shape`") all define real branching
behavior — accept an edited dict, re-validate it, replace the proposal's shape.
No AT covers it (AT#5 tests bare approve/decline; AT#11/12 POST
`{"decision":"approve"}` with no `shape`). Either add an AT for the edited-shape
approve path (including a re-validation-fails case), or move `edited_shape` to Out
of Scope for M0 and drop the parameter. As written it is an untested in-scope
branch an implementor could ship wrong silently.

**M2 — `dashboard-shape-gate` is the heaviest task and borders on a mega-task.**
It bundles `_build_state`/`_build_local_instance` changes, a new Starlette route +
`_handle_shape_approval` + `_proxy_shape_approval`, the `dashboard.html` gate
panel with mermaid wiring, *and* both a multi-instance httpx test and a Playwright
graphical-render/XSS test (3 ATs across backend route, front-end render, and two
test harnesses). It coheres around one surface (the dashboard), so it is
defensible, but it is the one task a single reviewer may struggle to hold whole.
Consider splitting backend (route + proxy + state, AT#11/12) from front-end
(mermaid panel + XSS, AT#13) — they have a natural seam at the `/api/state`
contract. Not a blocker; flagged for decomposition quality.

### Low

**L1 — Type mismatch: `ShapeNode.extra_tools`/`extra_skills` are `tuple[str,…] | None`, but `broker.spawn_teammate` expects `list[str] | None`.**
`instantiate_shape` must convert tuple→list when forwarding. Trivial, but worth a
one-line note so the implementor doesn't pass a tuple into a `list`-typed param.

**L2 — `phases: tuple[dict, …]` sits in tension with the "reject unknown keys" rule.**
The schema rejects unknown keys at shape/node/edge level (AT#4d) but `phases` is
free-form `dict` recorded unvalidated. Reasonable (lifecycle metadata is M-future),
but state explicitly that `phases` entries are *not* key-validated so the
implementor doesn't apply the unknown-key guard to them.

## Acceptance-Test Coverage (Breakout)

| AT | Task | Status |
|----|------|--------|
| 1,2,3,4 | shape-schema-parser | claimed once |
| 5,6,7 | broker-proposals-topology | claimed once |
| 8,9,10 | shape-mcp-tools | claimed once |
| 11,12,13 | dashboard-shape-gate | claimed once |

All 13 ATs covered exactly once; no duplicates, no unclaimed ATs. Dependency DAG
is sound: schema → broker → {mcp-tools, dashboard}, with mcp-tools and dashboard
correctly **independent** of each other (the dashboard tests inject a pending
proposal directly into a broker and never call the MCP tools) and touching
disjoint files (`server.py`+`test_shape_gate.py` vs.
`ui_server.py`/`dashboard.html`+`test_shape_dashboard.py`) — safe to parallelize.
No spurious edges. No mega-task hard-fail (see M2). Behavior-coverage gaps are
captured in H1/M1 above — these are missing *tests*, not missing AT-to-task
assignments.

## Test Command

`uv run pytest` — runnable as written; the full-suite choice is correctly
justified (new `BrokerSnapshot` fields + tool-surface growth are cross-cutting,
per the repo's widely-consumed-behavior rule). AT#13's Playwright prereq
(`uv run playwright install chromium`) is called out. Per-task `testCommand`s are
present and consistent.

## Verification Notes

- `claude_crew/ui/dashboard.html`: `mermaid.render` / `renderMermaidBlocks` /
  `securityLevel:'strict'` / DOMPurify-with-foreignObject confirmed (lines
  466–1094, 1363). Reuse claim is accurate.
- `ui_server.py`: `_proxy_artifact` (774), `_PATH_PARAM_RE` (47),
  `_own_crew_id` (177), local-vs-proxy routing (656–663, 747–753) confirmed.
- `broker.py`: `_lead_message_condition` Condition + `notify_all` long-poll
  precedent (158, 715, 829) confirmed; `spawn_teammate` signature (185–199)
  matches the spec's call.
- `server.py`: `spawn_teammate` tool (138–269) does **not** pre-validate `role`
  → grounds H1. `discover_dir(...).keys()` pack enumeration exists at 584.
- ARCHITECTURE.md multi-instance rule aligns with the spec's `crew_id`+proxy design.
