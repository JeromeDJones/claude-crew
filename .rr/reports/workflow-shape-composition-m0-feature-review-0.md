# Feature Review: workflow-shape-composition-m0

**Cycle:** 0
**Verdict:** PASS
**Scope:** Cross-slice synthesis only — integration coherence, holistic spec satisfaction, cracks-fell-through. Per-slice adherence already covered by 5 PASS slice-reviews. Non-regression established by coordinator ground truth (`uv run pytest` → **1424 passed, 34 skipped, 1 xfailed, exit 0**).

---

## Check 1 — Integration Coherence (the seams)

All five slices fit. I traced every seam end-to-end against the live source.

### Seam A — `shapes.py` → broker / server / ui_server / dashboard
- `parse_shape(data, *, source)` and `shape_to_mermaid(shape)` signatures match the spec's Data/API contract exactly. `Shape/ShapeNode/ShapeEdge` frozen dataclasses carry the declared fields (slot, role, model, extra_tools, extra_skills, cwd / from_slot, to_slot, mode="gated", reverse_mode / name, description, nodes, edges, phases=()).
- `server.py` imports `parse_shape` + `ShapeValidationError`; `ui_server.py` imports `shape_to_mermaid`. No alternate parser, no second mermaid emitter — single producer per concern.
- **Mermaid contract lines up across the HTTP seam:** `shape_to_mermaid` emits `graph TD` with `slot["slot\nrole"]` nodes and `from -->|mode| to` edges. `ui_server._build_local_instance` pre-renders this into `shape_proposals[].mermaid`; `dashboard.html ShapeProposalCard` feeds that string into `<code className="language-mermaid">` → `renderMermaidBlocks(el)` → the shipped `mermaid.render()` + DOMPurify pipeline. Producer→carrier→renderer is coherent.

### Seam B — `broker.py` state → server tools + ui_server state
- `register_proposal` (pending) → `await_proposal` (asyncio.Condition long-poll, sets `timed_out` on `asyncio.timeout`) → `resolve_proposal` (approve/decline, `notify_all`) → `get_proposal` / `record_topology` / `get_topologies`. Methods and signatures match the spec.
- `BrokerSnapshot` gains `shape_proposals` + `topologies` (defaulted-empty, `snapshot()` tuple-izes both — same `startup_diagnostics` threading precedent). `ui_server` reads `snapshot.shape_proposals`; `server.instantiate_shape` constructs `Topology` and calls `record_topology`. No field drift.

### Seam C — `server.py` pre-flight vs. `factories._resolve_role` (the one substantive risk)
This is the seam slice-3 explicitly handed up. Verified directly:
- **Resolvable case (exact match or unique `*:role` suffix):** pre-flight and `_resolve_role` are **identical** — both accept on `role in known` or exactly one `k.endswith(f":{role}")`.
- **Ambiguous (`len>1`) / unknown (`len==0`) case:** pre-flight **refuses** (`unresolved_roles`, zero spawn); `_resolve_role` falls through to the bare role → synthetic empty AgentDef (degenerate spawn). Pre-flight is **strictly more conservative**.
- **Conclusion:** the divergence is one-directional and safe — pre-flight never admits a role that spawn-time would choke on; it only refuses (correctly, per all-or-nothing) roles that spawn-time would have degenerate-spawned. No correctness crack. The logic *duplication* is a maintainability Info (see Check 3), not an integration defect.

### Seam D — Proposal lifecycle end-to-end
`propose_shape` → `register_proposal` → `await_proposal` (blocks) → dashboard/test `resolve_proposal` → `instantiate_shape` (refuses any `status != "approved"`; on success spawns N, records Topology, sets `status="instantiated"`). Single-use holds: a second `instantiate_shape` sees `instantiated` and refuses. Round trip is closed.

### Seam E — HTTP seam: `ui_server` ↔ `dashboard.html`
Field-by-field and URL-by-URL alignment confirmed:
- State emits `{shape_id, crew_id, status, adaptation_diff, mermaid}`; dashboard reads exactly those names.
- POST URL `/shape-approval/{crew_id}/{shape_id}` on both sides; body `{decision}` on both sides; `decision ∈ {approve, decline}` enforced at handler **and** broker.
- **crew_id routing consistency (slice-4 lead):** state `crew_id = snapshot.crew_id`; handler routes on `crew_id == self._own_crew_id()`, and `_own_crew_id()` is `self._broker.snapshot().crew_id` — **same source**. Local-vs-proxy decision is therefore self-consistent for the owning instance. Proxy (`_proxy_shape_approval`) mirrors `_proxy_artifact`: 404 on unknown/unregistered crew, 502 on unreachable follower, re-enters the follower's local path (no proxy loop). `_PATH_PARAM_RE` guards both path params (400). Seam is sound.

---

## Check 2 — Holistic Spec Satisfaction

The assembled feature delivers the spec's thesis: *author a shape → gate it as a graphical DAG on human approval → instantiate exactly the approved crew (refusing unresolvable roles) → read recorded topology back.* Both load-bearing invariants hold across slices:

- **Invariant (a) — the shape IS the gate.** `instantiate_shape` refuses every non-`approved` status (unknown / pending / declined / timed_out / instantiated) and returns before any `spawn_teammate`. Nothing spawns without a human (or stubbed) `resolve_proposal("approve")`. ✓ AT#9, AT#10.
- **Invariant (b) — all-or-nothing pre-flight.** Full node loop accumulates `unresolved` and returns `{ok:False, unresolved_roles}` *before* the spawn loop begins — the resolvable node is not spawned either. ✓ AT#14.

All 14 ATs trace to a slice with a PASS slice-review, and the integrated full suite is green (exit 0), including the dashboard httpx route tests (AT#11/12) and the Playwright render+XSS test (AT#13). Out-of-scope items (edge enforcement, in-gate editing, blessed library) are correctly absent — `resolve_proposal` has no `edited_shape` param, edge `mode` is recorded-only, `propose_shape` takes a dict not a path.

---

## Check 3 — Cracks-Fell-Through

I ran down every cross-slice lead from the slice-reviews. None rises to High/Critical; all are Info/Low and consistent with declared M0 scope. Captured here for the backlog:

1. **[Info] Pre-flight ↔ `_resolve_role` logic duplication** (slice-3). The `*:role` suffix promotion is re-implemented in `server.instantiate_shape` rather than shared. Verified behaviorally safe (Check 1, Seam C) — pre-flight is conservatively stricter. Risk is future drift if `_resolve_role` changes; a shared helper would harden it. Backlog candidate.
2. **[Info] `Topology` frozen-but-mutable `slot_to_teammate` dict** (slice-2). `@dataclass(frozen=True)` only blocks rebinding, not in-place mutation. In practice `server` builds the dict locally, hands it to `Topology`, and never mutates it afterward; `snapshot()` shares the (effectively immutable) object. No live mutation path. Consider `MappingProxyType` or a copy-on-record if a future writer appears. Harmless today.
3. **[Low] `resolve_proposal` has no source-state guard** (slice-2). It will flip `timed_out`→`approved`. Practical exposure is tiny: `propose_shape` has already returned `timed_out` to the lead, so the lead re-proposes rather than instantiates; and a late approve still requires a deliberate human action (approval *is* the gate). Not a spawn-without-approval hole. Worth a `pending`-only guard in a later milestone.
4. **[Info] Partial-spawn window outside role resolution** (slice-3). Pre-flight guarantees no partial spawn *due to unresolvable roles* (the declared AT#14 promise); a mid-loop `spawn_teammate` raising for an unrelated reason would leave already-spawned nodes. Outside M0's stated all-or-nothing scope (which is role resolution). Backlog note for a future transactional-spawn pass.
5. **[Info] Direct `proposal.status = "instantiated"` mutation** (slice-3). `instantiate_shape` mutates the live proposal object rather than calling a broker method — minor encapsulation smell; functional and test-covered.
6. **[Info] Gate panel renders active-instance proposals only** (slice-5). `ShapeGatePanel` reads `cli.shape_proposals` (selected instance). A follower's pending proposal surfaces only when that instance is selected — though the multi-instance **approval** path (proxy route, AT#12) works regardless. This is a product/UX question (should the leader aggregate pending gates across instances?), not a correctness defect for M0. Flag for product.

---

## Severity Tally
- Critical: 0
- High: 0
- Medium: 0
- Low: 1 (resolve_proposal source-state guard — deferred, non-blocking)
- Info: 5

No Critical or High findings. Integration is coherent, both invariants hold end-to-end, the 14 ATs are jointly satisfied, and non-regression is established. The Info/Low items are M0-appropriate and routed to the backlog.
