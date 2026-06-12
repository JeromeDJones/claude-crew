# Doc-Sync Checklist: workflow-shape-composition-m0

**Feature**: workflow-shape-composition-m0
**Cycle**: 0
**Date**: 2026-06-11
**Spec**: `.rr/specs/workflow-shape-composition-m0.md`
**Evidence**: 5 slice-review reports (all PASS), feature-review report (PASS), validation report (PASS, exit 0, 1424 tests)

---

## Target File Status

| File | Action | Status |
|------|--------|--------|
| `CLAUDE.md` | Edited — server.py tool count 12→14; `shapes.py` entry added; `broker.py` entry updated; `factories.py` entry updated | ✅ Applied |
| `doc/ARCHITECTURE.md` | Edited — Last Updated; server.py 12→14 tools; `shapes.py` section added; `broker.py` state-machine table added; `factories.py` `known_roles` documented; "Workflow Shape Composition (M0)" section added | ✅ Applied |
| `doc/BACKLOG.md` | Edited — `[2026-06-11]` section prepended (6 code findings + 4 infra/process observations) | ✅ Applied |
| `doc/PRODUCT-VISION.md` | Edited — Features Implemented + Next up lines updated; "Workflow Shape Composition" pipeline section added (wsc-m0/m2/m1/m3/m4/m5 rows); journal entry 2026-06-11 added | ✅ Applied |
| `doc/sdk-teammate-wiring.md` | No changes — feature adds lead MCP tools and pure-data schema; no SDK teammate wiring change | ✅ No-op (correct) |

---

## `CLAUDE.md` — Accepted Edits

### server.py tool count and new tools
- **Change**: "Exposes 12 tools" → "Exposes 14 tools"; added `propose_shape` and `instantiate_shape` descriptions to the tool list.
- **Source**: spec §Architecture Overview (tool count 12→14); server.py additions verified by feature-review Check 1 Seam D.

### `shapes.py` new entry in Core components
- **Change**: New `**shapes.py**` entry inserted before `**broker.py**`. Content: Shape/ShapeNode/ShapeEdge frozen dataclasses, ShapeValidationError, parse_shape, shape_to_mermaid; "pure data — no broker/SDK dependency."
- **Source**: spec §Architecture Overview (`shapes.py` NEW); feature-review Seam A.

### `broker.py` entry — shape proposal/topology state
- **Change**: Appended to the existing broker.py description: `ShapeProposal` state machine (`pending`→`approved`/`declined`/`timed_out`→`instantiated`, `asyncio.Condition` long-poll) and `Topology` (edges + slot→teammate map), both on `BrokerSnapshot`.
- **Source**: spec §Data/API Contracts; slice-review broker-proposals-topology PASS; feature-review Seam B.

### `factories.py` entry — `factory.known_roles`
- **Change**: Appended `factory.known_roles` accessor description (zero-arg callable returning live `tuple(holder.pack.keys())`; used by `instantiate_shape` pre-flight; stub factory omits by default).
- **Source**: spec §Design Decisions (Pre-flight enumeration via `factory.known_roles`); plan-review-1 H1 resolution; slice-review shape-mcp-tools PASS.

---

## `doc/ARCHITECTURE.md` — Accepted Edits

### Last Updated date
- **Change**: `2026-06-09` → `2026-06-11`.

### `server.py` section — 12→14 tools + new table rows
- **Change**: "Exposes 12 MCP tools" → "Exposes 14 MCP tools". Added two rows to the tool table:
  - `propose_shape` — Register a `Shape` as a pending human-approval gate; blocks on `await_proposal` (default 600s); returns `approved`/`declined`/`timed_out`
  - `instantiate_shape` — Spawn exactly the approved crew; pre-flight role resolution all-or-nothing; records `Topology`; single-use per `shape_id`
- **Source**: spec §Architecture Overview; feature-review Check 2 (both load-bearing invariants verified).

### New `claude_crew/shapes.py` section
- **Change**: Added full section in Module Roles (before `broker.py`). Includes symbol table (Shape, ShapeNode, ShapeEdge, ShapeValidationError, parse_shape, shape_to_mermaid) with kind and notes columns.
- **Source**: spec §Data/API Contracts; slice-review shape-schema-parser PASS (all 10 spec watchpoints verified).

### `broker.py` section — shape proposal state machine
- **Change**: Added "shape proposal registry" and "recorded topologies" to the module description. Added method table (register_proposal, await_proposal, resolve_proposal, get_proposal, record_topology, get_topologies) with notes. Added BrokerSnapshot fields note.
- **Source**: spec §Data/API Contracts; slice-review broker-proposals-topology PASS.

### `factories.py` section — `factory.known_roles`
- **Change**: Added paragraph describing `factory.known_roles` accessor (live `holder.pack.keys()`, same pattern as `factory.startup_diagnostics`; stub omits by default).
- **Source**: spec §Design Decisions; plan-review-1 verified mechanism at factories.py:549.

### New "Workflow Shape Composition (M0)" section
- **Change**: Added section before "Verified SDK Behavioral Invariants" covering:
  - Data flow diagram (propose_shape → parse → register → /api/state → dashboard → POST → resolve → instantiate → spawn × N → record_topology)
  - Edge modes (recorded, not enforced in M0; enforcement is M2)
  - Multi-instance shape approval (crew_id routing, _proxy_shape_approval, 404 on unknown crew)
  - Four invariants (shape is the gate; all-or-nothing pre-flight; single-use; XSS-hardened DAG)
- **Source**: spec §Design Decisions, §Validation Contracts, §Edge Cases; feature-review Check 1 (all seams verified).

---

## `doc/BACKLOG.md` — Accepted Edits

### New `[2026-06-11]` section prepended
- **Change**: Added `## [2026-06-11] Feature: workflow-shape-composition-m0` section at the top of entries (after the `---` intro separator). Contains:
  - 6 code findings (1× Low, 5× Info) pre-routed from the feature-review "cracks-fell-through" section
  - 4 coordinator/infra observations (1× High tooling, 1× Medium tooling, 1× Medium skill/coordinator, 1× Low prompt)
- **Source**: feature-review-0.md Check 3 (findings 1–6); coordinator process observations supplied in the documenter invocation.

---

## `doc/PRODUCT-VISION.md` — Accepted Edits

### Features Implemented header line
- **Change**: Appended `+ workflow-shape-composition-m0` to the Features Implemented line.
- **Source**: validation report (PASS, exit 0, 1424 tests).

### Next up line
- **Change**: `TBD — #20 peer messaging backlogged 2026-05-17 (coordinator-in-the-loop is the moat; see row 20 for rationale)` → `workflow-shape-composition-m2 (edge routing enforcement + scoped send_to + neighbor injection + circuit breaker; agreed ordering: M0 done → M2 next → M1 later — Jerome, 2026-06-11)`.
- **Source**: coordinator process observations (roadmap note, Jerome, 2026-06-11).

### Workflow Shape Composition pipeline section
- **Change**: New `### Workflow Shape Composition` section inserted before `### Deferred (v2+)` with six rows:
  - `wsc-m0` — done (2026-06-11) — M0 details + test/file counts
  - `wsc-m2` — next — M2 scope + M0 rail dependency
  - `wsc-m1` — deferred (after M2) — M1 scope + ordering rationale
  - `wsc-m3` / `wsc-m4` / `wsc-m5` — deferred
- **Source**: spec §Out-of-scope (M1–M5 descriptions); coordinator process observations (M2-first ordering, Jerome, 2026-06-11).

### Product Journal entry — 2026-06-11
- **Change**: New `### 2026-06-11 — Workflow Shape Composition M0 — Shipped` journal entry inserted before the `### 2026-06-09 — teammate-death-diagnostics — Shipped` entry. Covers: what shipped (all five modules), architecture decisions, test coverage, roadmap clarification, vision shift, pipeline impact.
- **Source**: spec §Architecture Overview, §Design Decisions, §Acceptance Tests; feature-review-0.md Check 2; validation report; coordinator roadmap note.

---

## `doc/sdk-teammate-wiring.md` — No Changes

No edits needed. The feature adds lead MCP tools (`propose_shape`, `instantiate_shape`) and a pure-data schema module (`shapes.py`). It does not change how SDK teammates wire their tools, skills, MCP servers, or memory. The teammate wiring asymmetry documented in `sdk-teammate-wiring.md` is unchanged.

---

## doc/ARCHITECTURE.md — Harvest Contract

`doc/ARCHITECTURE.md` existed prior to this feature (created in the `teammate-death-diagnostics` retro, 2026-06-09). This retro **updated** it (did not create it). The harvest contract for an update:
- ✅ Last Updated date updated to 2026-06-11
- ✅ New module (`shapes.py`) added to Module Roles
- ✅ Existing module descriptions updated (broker.py, factories.py, server.py)
- ✅ New feature section added ("Workflow Shape Composition (M0)")
- ✅ No existing verified invariants removed or contradicted

---

## Manual Checks (human-judged, not auto-applied)

These items were verified by the reviewer chain and are noted here for completeness but require no further doc action:

- **AT#13 manual Mission Control check** — pending Jerome: start the server, call `propose_shape` with a 2-node shape, confirm the shape-gate panel renders a graphical mermaid DAG (2 labeled nodes + a `gated`-labeled wire) with Approve/Decline; Approve unblocks the lead; `instantiate_shape` spawns exactly those 2 teammates, which appear in the live crew view. FAIL criteria: DAG never renders or renders as unlabeled black boxes; buttons do not resolve the proposal; instantiate spawns a crew that differs from the approved shape. *(Automated coverage already green: multi-instance proxy, visible-label render, XSS guard, all-or-nothing refusal.)*
