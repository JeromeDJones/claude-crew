# Feature: m3-adaptation-algebra — Workflow Shape Composition M3

**Status:** SHIPPED — 2026-06-17
**Validation:** PASS — 1613 passed, 34 skipped, 1 xfailed, 61 warnings (215.34s full-suite run)
**Feature tests:** +83 (shape-adaptation-algebra 45, adapt-shape-tool 38)
**Cycles:** 2 tasks × 1 review cycle each (spec: 2 plan-review cycles)

---

## Purpose

M3 makes shape adaptation **computable**. Before M3, adapting a crew topology meant discarding the existing `Shape` and hand-authoring a new one from scratch. M3 introduces five typed verb commands — each a pure `Shape → (Shape, AdaptationDiff)` transformer — and a single `adapt_shape` MCP tool that applies any verb to a pre-instantiation shape and gates the result on the existing M1.5 human gate. Policy consumers (repo-react right-sizing) own *which* adaptation to apply; claude-crew owns the *mechanism*.

---

## Acceptance Tests

| # | Description | Task |
|---|-------------|------|
| AT 1 | `AddNode` happy-path: new node + edge added; base unchanged; diff verb == "add_node" | shape-adaptation-algebra |
| AT 2 | `Swap` happy-path (no optionals): role replaced; slot/model/extra_tools/extra_skills/edges unchanged | shape-adaptation-algebra |
| AT 3 | `Augment` happy-path: new node + wiring edge added; edge mode defaults to "gated" | shape-adaptation-algebra |
| AT 4 | `SetGate` happy-path: edge mode changed; diff target, before/after correct | shape-adaptation-algebra |
| AT 5 | `Drop` happy-path: node removed; remaining nodes/edges unchanged; diff verb == "drop" | shape-adaptation-algebra |
| AT 6 | Round-trip invariant: `parse_shape(shape_to_dict(result)) == result` for all AT 1–5 results + AT 35 rich-field result; `shape_to_dict` preserves phases, cwd, model, extra_tools, extra_skills, reverse_mode | shape-adaptation-algebra |
| AT 7 | `render()` golden: each verb emits the documented format string; AT 35 appends per-optional `; {field} {old} -> {new}` clauses | shape-adaptation-algebra |
| AT 8 | `AdaptationChain` provenance: two-step chain has ordered steps, `current` == twice-adapted shape | shape-adaptation-algebra |
| AT 9–22, 34 | Structural sad-paths: each verb raises `ShapeValidationError` on its enumerated illegal inputs; base unchanged | shape-adaptation-algebra |
| AT 35 | `Swap` optional-field replacement: role + model + extra_tools + extra_skills all replaced; slot/incident-edge/phases/cwd unchanged; diff before/after populated (incl. `extra_skills: None`); `render()` emits three `; {field}` clauses | shape-adaptation-algebra |
| AT 23 | Gate integration: inline base + set_gate → proposal registered; `adaptation_diff` populated | adapt-shape-tool |
| AT 24 | Iterative re-gate loop: swap → approve → set_gate with `base_shape_id` → distinct new proposal | adapt-shape-tool |
| AT 25–28 | Role resolution: resolvable swap (ok:True), unresolvable swap (stage:adapt), unresolvable augment (stage:adapt), no-known_roles skip (ok:True) | adapt-shape-tool |
| AT 29–31 | Base guards: unknown id, instantiated/declined/timed_out status, neither/both args → stage:base | adapt-shape-tool |
| AT 32 | Unknown verb → stage:verb | adapt-shape-tool |
| AT 33 | ShapeValidationError in verb → stage:adapt; no proposal registered | adapt-shape-tool |

---

## Tasks

### Task 1: `shape-adaptation-algebra` (PASS, cycle 0)

**Scope:** Pure-data adaptation algebra in `claude_crew/shapes.py`.

**Deliverables:**
- `_UNSET` module sentinel (distinguishes "not supplied" from `None`)
- `AdaptationDiff` frozen dataclass with `render()` (golden format per verb; `before`/`after` excluded from hash)
- `ShapeAdaptation` ABC: `apply(shape) -> tuple[Shape, AdaptationDiff]`
- `AddNode`, `Swap`, `Augment`, `SetGate`, `Drop` — five frozen verb dataclasses; each validates all enumerated sad paths; `Swap` uses `_UNSET` for optional fields; `SetGate` uses `_UNSET` for `reverse_mode`
- `AdaptationStep` and `AdaptationChain` frozen dataclasses; `AdaptationChain.adapt()` returns a NEW chain; errors propagate naturally; prior chain untouched on failure
- `shape_to_dict(shape) -> dict` — inverse of `parse_shape`; tuples→lists for JSON compat; preserves all optional fields
- `tests/test_shape_adaptation.py` — 45 tests covering ATs 1–22, 34, 35

**Slice review finding (Info):** `AddNode.apply` and `Augment.apply` carry near-identical edge-validation loops. Spec explicitly defers helper extraction. Not a defect.

### Task 2: `adapt-shape-tool` (PASS, cycle 0)

**Scope:** `adapt_shape` MCP tool in `claude_crew/server.py`. `broker.py` **not modified**.

**Deliverables:**
- Updated import of shapes-algebra symbols in `server.py`
- `adapt_shape` tool implementing five-stage flow: base resolution → parse → verb guard → role resolution → verb apply → register_proposal
- `_node_from_dict` local helper (list→tuple coercion for node params)
- Failure envelopes for each stage; `register_proposal` called exactly once at the end of the success path
- `tests/test_shape_adapt_tool.py` — 38 tests covering ATs 23–33

**Slice review findings (Low, spec-sanctioned):** `_KNOWN_VERBS` frozenset rebuilt per call; role-resolution site duplicated from `instantiate_shape`. Both deferred by spec.

---

## Files Changed

| File | Change |
|------|--------|
| `claude_crew/shapes.py` | +550 lines — full adaptation algebra (see Task 1) |
| `claude_crew/server.py` | +182 lines — `adapt_shape` MCP tool + updated imports |
| `tests/test_shape_adaptation.py` | NEW — 45 tests (ATs 1–22, 34, 35) |
| `tests/test_shape_adapt_tool.py` | NEW — 38 tests (ATs 23–33) |

`claude_crew/broker.py` — **unchanged.** `register_proposal(shape, adaptation_diff?)` already carried the channel.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| One `adapt_shape` tool with a verb discriminator | Smaller uniform MCP surface than five per-verb tools; mirrors `propose_shape`; per-verb validation lives inside each verb's `apply` |
| `Shape` stays a pure frozen dataclass; verbs are command/transform objects | Every `Shape` consumer (`instantiate_shape`, `shape_to_mermaid`, `Topology`) expects a concrete `Shape`; a wrapper would be flattened at every gate |
| `apply` is pure: no mutation, returns new frozen `Shape` or raises loudly | No silent no-op, no partial shape; every illegal mutation is a `ShapeValidationError` |
| `broker.py` untouched | `register_proposal` already accepts `adaptation_diff?`; M0/M1.5 gate was built anticipating M3 |
| Role resolution at server layer, not inside `apply` | `shapes.py` has no broker/SDK/factory dependency by design; resolution lives in `server.py`, exactly as `instantiate_shape`'s pre-flight |
| `swap` optional fields: supplied → replace, omitted → retain (`_UNSET`) | Enables role + config re-targeting in one verb; the retained-vs-replaced distinction is a named deliverable (AT 35) |
| `adapt_shape` success `shape` field is `shape_to_dict` dict form | MCP tool returns must be JSON-serializable; raw frozen `Shape` is not |
| `AdaptationChain` in-process only; not persisted | M3 has no cross-call observability requirement; broker persistence deferred until one exists (tracked in backlog candidates) |
| `augment` requires ≥1 wiring edge; edge mode defaults to `"gated"` | Augment without an edge is just `add_node`; `gated` matches `ShapeEdge`'s default |
| Pre-instantiation only | `adapt_shape` operates on `Shape` data and never touches the live teammate registry; reshaping a running crew is M3.5 |
| Role-resolution duplication (adapt_shape + instantiate_shape) | Two structurally-identical sites; extraction deferred by spec (Out of Scope) |

---

## Known Gaps / Backlog

See `m3-adaptation-algebra-retro-backlog-candidates.md` for the three retro findings:

- **[Low, tooling]** Widen `adaptation_diff` gate channel from `str` to structured `AdaptationDiff` (C-01)
- **[Low, tooling]** Persist adaptation chains to broker / transcript for cross-call observability (C-02)
- **[Low, tooling]** Fix intermittent `test_shutdown_signals.py` startup-race flake (C-03, pre-existing)

Out-of-scope items on `doc/ROADMAP.md`:
- M3.5 — reshape live/instantiated crew (🔜 next)
- M4 — RepoReactor as declared shape (⏳ deferred)
- M5 — memory-informed autonomous adaptation (⏳ deferred)
