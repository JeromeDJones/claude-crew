# M3 — Adaptation Algebra (design brief)

**Status:** design-locked, ready for `/repo-react`. **Author:** Kael, 2026-06-17.
**Arc:** Workflow Shape Composition (M0 ✅ → M1.5 ✅ → M2 ✅ → unified-topology-view ✅ → **M3**).
**Roadmap:** `doc/ROADMAP.md` § M3. **Design decisions below are confirmed with Jerome (2026-06-17).**

---

## 1. Problem & goal

Today a `Shape` is composed once (`propose_shape`) and instantiated (`instantiate_shape`). There is no computable way to *adapt* an existing shape — to say "swap this reviewer," "gate this edge," "drop this node" — and have the change presented for approval. Adaptation today would mean hand-authoring a whole new shape.

M3 makes shape adaptation **computable**: five typed verbs that each transform a `Shape` into a new `Shape` plus a structured **`AdaptationDiff`**, with every adaptation presented at the existing human gate before it takes effect. This is the *mechanism* that policy consumers (repo-react's blessed-shape right-sizing) build on — claude-crew owns the algebra; consumers own which adaptation to apply.

## 2. The interaction model (confirmed — fork 1)

Iterative, gated, one approval per adjustment:

1. Operator/lead has a base shape (a pending or approved proposal, or an inline shape).
2. An adaptation verb is applied → produces `(new_shape, AdaptationDiff)`.
3. The new shape is registered as a **new pending proposal carrying its `AdaptationDiff`**, reusing the M1.5 async gate. The dashboard / `resolve_shape` shows **the diff** (what changed), not a from-scratch re-review.
4. Human approves (or declines). On approval the adapted shape is the new current shape.
5. A second adjustment repeats 2–4 — **shown again for approval.** Each step in the chain is independently gate-approved.

**This reuses the existing gate verbatim.** `propose_shape` / `broker.register_proposal` already carry an `adaptation_diff` parameter (server.py:738, broker.py:1367) — the M0/M1.5 gate was built anticipating M3. M3 fills that channel; it does **not** invent a second approval path. Autonomous (un-gated) adaptation stays out of scope — that is M5 ("earn the rope").

## 3. Implementation shape (confirmed — fork 2: command/transform, NOT Shape-decorator)

**`Shape` stays a pure frozen dataclass — unchanged.** Verbs operate *on* it and return a *new* frozen `Shape`. We deliberately do **not** use a decorator that wraps `Shape` and exposes its interface: that fights the frozen-data model and every `Shape` consumer (`instantiate_shape` pre-flight, `shape_to_mermaid`, broker `Topology` recording) expects a concrete `Shape`, so a wrapper would be flattened at every gate/instantiation anyway — complexity with no laziness payoff (we materialize each step to render + gate it).

Instead, three pieces, all in `claude_crew/shapes.py` (pure-data module, no broker/SDK dep — same home as `parse_shape`/`shape_to_mermaid`):

1. **Verb command objects.** A tiny abstract base `ShapeAdaptation` with a uniform `apply(shape: Shape) -> tuple[Shape, AdaptationDiff]`, one concrete class per verb. Each class encapsulates its own validation and its own diff construction. Construct from typed params; `apply` is pure (no mutation; returns a new frozen `Shape`).
2. **`AdaptationDiff`** — a structured frozen dataclass: `verb`, `target` (slot or edge key), `before` / `after` summary (the changed fields only), and a human-readable `render() -> str`. The structured form powers golden tests + provenance; `render()` produces the string fed into the gate's existing `adaptation_diff: str` channel (so **no gate signature change** — back-compat preserved). *Non-goal:* widening the gate channel to carry the structured object for richer dashboard rendering — note it as a future option, not M3.
3. **Adaptation chain (provenance).** An ordered record of applied adaptations — `tuple[(verb, params, resulting_shape, AdaptationDiff), ...]` — modeling Jerome's "describe → adapt → approve → adapt → approve" sequence. Gives full provenance ("how did we reach this shape") and trivial replay/test. (Exact carrier — returned value vs. a lightweight `AdaptationChain` dataclass — is an open question for the planner, §7.)

## 4. The five verbs (confirmed — fork 3: ALL FIVE in-slice, no deferred work)

Each verb: typed params, pure `apply`, happy-path correctness + illegal-mutation rejection (loud `ShapeValidationError`, never a silent no-op or partial shape).

| Verb | Params | Effect | Illegal-mutation rejections (sad paths) |
|------|--------|--------|------------------------------------------|
| `add_node` | `ShapeNode` + optional incident edges | Add a new slot (+ optional edges) | duplicate slot; edge to/from a non-existent slot; self-loop (mirror `parse_shape` rules) |
| `swap` | `slot`, new `role` (+ optional model/tools/skills) | Replace a node's role (slot identity + incident edges unchanged) | slot does not exist; **new role unresolvable** against `factory.known_roles` / `resolve_role` |
| `augment` | `ShapeNode` + edge(s) wiring it to an existing slot | Add a participant alongside an existing node (e.g. a second reviewer) | target slot does not exist; **augmenting role unresolvable**; resulting duplicate slot/edge |
| `set_gate` | edge `(from_slot, to_slot)`, new `mode` (+ optional `reverse_mode`) | Change an edge's routing mode | edge does not exist; mode not in `{gated,tee,direct}` |
| `drop` | `slot` | Remove a node | slot does not exist; **node has live in-edges or out-edges** (must `set_gate`/rewire or drop edges first — no dangling-edge shapes) |

**`swap` / `augment` role-resolution is IN-SLICE (no deferral).** They resolve the new/added role through the same seam `instantiate_shape` uses — `factory.known_roles()` + `factory.resolve_role()` (server.py:919-934), which now spans the merged pack incl. the shipped extensible-roles replacement surface. Unresolvable role → loud rejection at adapt time (before the gate), mirroring instantiate's all-or-nothing pre-flight. When the factory exposes no `known_roles` (stub-mode tests), resolution is skipped exactly as instantiate does.

Every verb's output `Shape` must re-satisfy `parse_shape`'s invariants (unique slots, edges reference existing slots, no self-loops, no duplicate edges, valid modes) — i.e. **a verb can never produce a shape `parse_shape` would reject.** This is a cheap, strong correctness property to test (round-trip each adapted shape through validation).

## 5. Server / broker surface

- **`claude_crew/server.py`** — new lead MCP tool(s) to apply an adaptation. Open question (§7): one `adapt_shape(base, verb, params)` tool with a verb discriminator, vs. five per-verb tools. Either way the tool: resolves the base shape → applies the verb command → on success `register_proposal(new_shape, adaptation_diff=diff.render())` (reusing the M1.5 gate) → returns `{ok, shape_id, status:"pending", diff}`; on illegal mutation returns `{ok:False, stage:"adapt", error}` (no proposal registered). Extends the current 16-tool surface.
- **`claude_crew/broker.py`** — no new gate machinery needed (`register_proposal` already takes `adaptation_diff`). Only addition if the planner chooses to persist the adaptation chain on broker state for provenance/observability (§7 open).
- **No `dashboard.html` work required for M3** — the diff already surfaces via the existing `adaptation_diff` string channel. (Richer structured-diff rendering = explicit non-goal / future.)

## 6. Out of scope

- **M5 autonomy** — lead-autonomous / memory-informed adaptation. M3 is human-gated, every step.
- **M4** — re-authoring repo-react as a blessed shape.
- **Blessed shape library + classifier (former M1)** — now repo-react's policy responsibility, not claude-crew's. M3 is the mechanism it will consume.
- **Widening the gate `adaptation_diff` channel** to carry the structured object + bespoke dashboard diff rendering — future option; M3 renders to the existing string channel.
- **Persisting/replaying chains across sessions** — provenance is in-process for M3.
- **Reshaping a LIVE (already-instantiated) crew** — adapting a topology whose teammates are already spawned (kill/respawn on `swap`/`drop`, rewire live routing mid-flight). This touches the broker's live teammate registry + M2's routing engine, not just the pure `Shape` data — a materially bigger, riskier feature. **Confirmed with Jerome (2026-06-17): this is its own milestone, M3.5 ("reshape crew while running"), handled separately.** M3 adapts shapes only *before* instantiation.

## 7. Open questions for the planner / spec

1. **Tool surface:** one `adapt_shape` tool (verb discriminator + params dict) vs. five per-verb tools. Lean: one tool (smaller surface, uniform), but the planner should weigh discoverability + arg-validation clarity.
2. **RESOLVED (Jerome, 2026-06-17): base shape is PRE-INSTANTIATION only** — an adapt call targets a pending proposal (`shape_id`), an approved-but-not-yet-instantiated shape, or an inline shape. It MUST NOT target a live/instantiated topology — reshaping a running crew is M3.5 (§6). So an adapt verb operates purely on `Shape` data; it never touches the live teammate registry or routing.
3. **Adaptation-chain carrier:** plain returned tuples vs. a lightweight `AdaptationChain` dataclass vs. persisted on broker state. Lean: a `shapes.py` dataclass returned/threaded, broker-persistence only if observability needs it.
4. **`augment` edge semantics:** exact required params for wiring the augmented node (one edge? mode default?).

## 8. Test surface (deletion-detecting where feasible)

- Per-verb happy-path: pre/post `Shape` correctness (golden assertions on the resulting frozen shape).
- Per-verb sad-path: each illegal-mutation row in §4 raises loudly (no silent no-op, no partial shape).
- **Round-trip invariant:** every adapted shape passes `parse_shape` (re-serialize → parse → equal).
- `swap`/`augment` role-resolution: resolvable role succeeds; unresolvable role rejected (with a `known_roles`-injected stub factory, mirroring the instantiate pre-flight tests).
- `AdaptationDiff.render()` golden tests (the string the gate shows).
- Gate integration: an adapt call registers a pending proposal carrying the diff; `resolve_shape` approves it; a second adapt on the approved shape registers again (the iterative re-gate loop).
- Full `uv run pytest` (widely-consumed substrate change — run the whole suite, not a subset).

## 9. Touch-points (grounded in current code)

- `claude_crew/shapes.py` — `ShapeAdaptation` base + 5 verb classes + `AdaptationDiff` (+ chain carrier). Pure data; no new deps.
- `claude_crew/server.py` — adapt tool(s); reuse `parse_shape` (L30), `register_proposal(..., adaptation_diff=)` (L778), and the `known_roles`/`resolve_role` resolution block (L919-934) for swap/augment.
- `claude_crew/broker.py` — `register_proposal` (L1367) already supports the diff; optional chain persistence only if §7.3 chooses it.
- `tests/` — new `tests/test_shape_adaptation.py` (verbs + diffs + role-resolution + round-trip); gate-integration assertions alongside the existing shape-gate tests.

---

**Process note:** run through `/repo-react` on claude-crew. Refresh the rr-* packs first — the running claude-crew froze them at startup on the 0.12.3 cache; the cache is now 0.16.1 (BC-02 heuristics + deletion-detection invariant + extensible-roles). `refresh_agents` or restart so the flow uses the hardened planner/reviewer contracts.
