# Workflow Shape Composition — feature draft & implementation guidance

Status: **rough draft, ready to hand to `/repo-reactor:repo-react` as the heavy-feature input.**
Date: 2026-06-11. Authors: Jerome (product) + Kael.

> **Read the thesis first:** [`workflow-shape-composition.md`](./workflow-shape-composition.md). This doc is the
> *how*; that doc is the *why* and carries the decisions, the moat argument, and the open questions. Section
> references below (e.g. *§Adaptation algebra*) point into it.

---

## 1. One-paragraph summary

Give the **lead** the ability to compose a **right-sized workflow shape** for the problem in front of it: pick a
**blessed shape** from a small library, optionally **adapt** it with a few bounded operations, get **human
approval of the shape itself** (the primary gate), then **instantiate and run** it — spawning the declared
agents, wiring the declared edges, enforcing the declared gates. RepoReactor becomes one blessed shape (the heavy
one), re-authored natively. Every running graph is "blessed shape + a small readable diff," fully observable on
Mission Control. See thesis *§The capability* and *§blessed shapes + bounded adaptation*.

## 2. Design invariants (do not violate — these are the moat)

1. **Observable by construction.** Every cross-teammate message routes through the broker, is sequenced, logged,
   and surfaced to Mission Control. No off-broker peer channels. (thesis *§Why this passes the acid test*)
2. **The shape is the primary gate.** No teammate spawns until the lead has surfaced the proposed graph (shape +
   adaptation diff) and a human has approved or tweaked it. The coordinator-in-the-loop moat operates at the
   **composition** layer, not just the message layer.
3. **Coordinator stays on judgment edges.** Per-edge policy may remove the lead from *relay*, never from *gate*.
   (thesis *§Edges carry a routing policy*)
4. **Bounded adaptation only.** Adaptation is the fixed verb set (§5), never freeform graph synthesis — that's
   what keeps every graph legible. Open synthesis is explicitly deferred. (thesis *§the fork that matters*)
5. **Bonded to the Claude Agent SDK.** Build on the existing SdkTeammate/broker substrate; do not adopt an
   external orchestration runtime (it would cost us the observability moat). (thesis *§Competitive / SDK posture*)

## 3. Implementation arc (bottom-up milestones — moat ships first, risk comes last)

Each milestone is independently shippable and leaves `master` green. The ordering is deliberate: the
shape-gate + observability scaffolding lands **before** any peer-comms capability, so the autonomous-chatter risk
is contained by the time it exists.

### M0 — Shape as data + the shape-gate (NO peer comms yet)
The keystone. Delivers right-sizing value with zero new comms risk.
- **Shape-template schema** (§4): nodes (role, model, tools/extra_tools, skills, cwd-policy), directed edges with
  a `mode` (all `gated` for now = today's behavior, just *declared*), optional phase/lifecycle metadata.
- New lead tool **`propose_shape(shape, adaptation_diff?)`** → renders the graph to Mission Control and **blocks
  on human approve/tweak** (the gate, invariant 2). Lean on the shipped mermaid/diagram-viz layer for the render.
- New lead tool **`instantiate_shape(shape_id)`** → spawns the declared teammates and records the topology in the
  broker (a new `Topology` structure: edges + per-edge policy, queryable).
- Edges are *recorded* but enforcement is trivial (all `gated` = unchanged routing). **No teammate `send_to`
  yet.**
- **Validation (two layers, happy+sad):** schema-parse unit tests (valid/invalid shapes); integration test —
  propose → approve (stubbed) → instantiate → assert the right crew spawned + topology recorded; sad — malformed
  shape rejected at parse, approval-declined aborts spawn.

### M1 — Blessed shape library + lead router
- Author three shapes as templates (thesis *§First shapes worth blessing*): `micro-fix`, `standard-feature`,
  `heavy-feature`. Store as declarative files (pack-adjacent, e.g. `shapes/*.yaml` or `.md`+frontmatter to match
  the agent-pack convention).
- Lead-side guidance (skill/prompt): classify the problem → pick a shape → `propose_shape`. The classifier is a
  prompt, not code — keep it cheap and overridable.
- **Validation:** golden-file tests that each blessed shape parses + instantiates to the expected crew; a
  routing-smoke test that a sample task maps to the expected shape.

### M2 — Edge routing policy + scoped teammate `send_to` (the comms substrate)
Only now, with the gate + observability in place to contain it.
- Broker: enforce per-edge `mode` on routing. `gated` (lead inbox first) / `tee` (direct + cc lead) / `direct`
  (direct, logged). Teammates gain a **`send_to` scoped to their declared out-edges only** — the topology IS the
  authorization layer (thesis *§Current structural reality*: today `recipient=env.sender` is hardcoded and
  teammates hold no send tools; this is the change).
- **Neighbor injection:** at spawn, inject each teammate's adjacency ("you can consult `plan-reviewer` (direct);
  verdicts arrive from `slice-reviewer` (gated)"). Without this the edges exist but never get used.
- **Circuit breaker** on `direct`/`tee` edges: max exchanges before lead is force-inserted, per-edge token/turn
  budget, deadlock detection (A waits B waits A). Non-negotiable — this is the autonomous-chatter guard.
- **Observability:** edges animate on the dashboard; click an edge → its message log; operator can **promote an
  edge to `gated`** mid-run to step onto it.
- **Validation:** per-mode routing unit tests; integration — a `direct` edge ping-pong stays off the lead's inbox
  but appears in the broker log + dashboard state; sad — exceeding the circuit-breaker budget force-inserts the
  lead; scoped-`send_to` rejects a non-declared recipient.

### M3 — Adaptation algebra
- Implement the verbs (§5): `add_node`, `swap`, `augment`, `set_gate`, `drop`. Each = a typed mutation on a shape
  producing a new shape + a **diff** rendered at the M0 gate.
- `swap`/`augment` ride the **extensible-roles** seam — build/finish `doc/plans/extensible-roles-idea.md` *as*
  the role-substitution primitive, not a one-off reviewer swap (thesis *§Connection to queued work*).
- **Validation:** each verb's pre/post shape correctness (happy) + illegal-mutation rejection (sad: swap into a
  non-existent slot, augment with an unknown role, drop a node with live in-edges); diff-render golden tests.

### M4 — RepoReactor re-authored as the `heavy-feature` blessed shape
- The ground-up rewrite Jerome called for (thesis *§Implication*). Re-express repo-react's planner →
  plan-review → task-DAG → implement → slice-review → feature-review → validate → document as a native shape with
  declared edges/gates, retiring the bespoke SKILL orchestration in favor of the shape engine.
- **Validation:** run a real feature through the re-authored heavy shape; assert parity with the legacy
  repo-react gates (no lost gate, no lost artifact). This is the acceptance test for the whole substrate.

### M5 — Memory-informed + lead-autonomous adaptation (DEFERRED — earn the rope)
- Adaptation reasons become evidence-driven (RR project-scoped memory → "the project's validator outperforms →
  propose `augment`"). Widen lead latitude from human-approved toward autonomous, once M1–M4 prove
  shapes-against-sizing. (thesis *§autonomy arc*; *§Open decisions* #1)

## 4. Shape-template schema (draft)

```yaml
name: standard-feature
description: plan -> implement -> review, no task DAG
nodes:
  - slot: planner            # logical slot name (adaptation targets this)
    role: rr-planner         # resolves to an agent-pack definition
    model: frontier          # frontier | local | explicit id
  - slot: implementor
    role: rr-implementor
    model: local
  - slot: reviewer
    role: rr-slice-reviewer
    model: local
edges:
  - from: planner
    to: implementor
    mode: gated              # gated | tee | direct
    reverse: { mode: direct } # optional back-edge for clarifications
  - from: implementor
    to: reviewer
    mode: gated
phases:                       # optional lifecycle: which edges live when
  - name: plan
    active_edges: [planner->implementor]
  - name: build
    active_edges: [implementor->reviewer]
```

Open: exact format (YAML vs md+frontmatter to match the pack), role→pack resolution, model-tier aliases
(`frontier`/`local`) vs explicit ids, fan-in/fan-out edge syntax (`implementors[] -> feature-reviewer`).

## 5. Adaptation algebra (draft API)

| Verb | Signature | Use |
|------|-----------|-----|
| `add_node` | `(shape, NodeSpec) -> shape` | insert a co-architect / extra reviewer |
| `swap` | `(shape, slot, role) -> shape` | replace a built-in role with the project's |
| `augment` | `(shape, slot, validator_role, edge_mode) -> shape` | attach a 2nd validator alongside a node |
| `set_gate` | `(shape, edge, mode) -> shape` | strengthen / loosen a gate |
| `drop` | `(shape, slot\|edge) -> shape` | trim for right-sizing |

Each returns a new shape + contributes to the **adaptation diff** shown at the gate. `swap`/`augment` depend on
project-extensible roles (M3 ↔ extensible-roles).

## 6. Touch-points in the current codebase

- `claude_crew/broker.py` — new `Topology` state (edges + policy); per-edge routing enforcement (today `send`
  routes by `recipient`; add policy gate); scoped-recipient authorization.
- `claude_crew/sdk_teammate.py` — teammates gain a scoped `send_to`; neighbor adjacency injected at spawn
  (today every reply is hardcoded `recipient=env.sender`, line ~1706).
- `claude_crew/server.py` — new lead tools `propose_shape` / `instantiate_shape` / adaptation verbs; extend the
  12-tool surface.
- `claude_crew/ui_server.py` + dashboard — shape-gate render + approve flow; edge animation; click-edge→log;
  promote-edge-to-gated control. **Remember the multi-instance leader/follower rule** (CLAUDE.md): any new
  per-instance endpoint must carry `crew_id` and proxy leader→follower.
- Shape library — new `shapes/` dir; blessed templates.
- Pack/roles — extensible-roles seam for `swap`/`augment`.

## 7. Risks & open decisions

- **Autonomous-chatter / token runaway** on `direct` edges → mitigated by M-ordering (gate ships first) + the M2
  circuit breaker. Do not ship `direct` without it.
- **Right-sizing classifier quality** — a prompt, cheap to iterate; the human gate backstops a wrong shape pick.
- **Auto-topology temptation** — keep adaptation bounded (invariant 4); open synthesis stays deferred pending the
  academic evidence (research in flight) that it underperforms templates.
- Open decisions carried in thesis *§Open decisions*: who decides "adapt if needed" (autonomy arc), shape
  format, gate render mechanics.
- **Default edge mode — ✅ RESOLVED 2026-06-11 (Jerome): `gated`-first, NOT `tee`.** New edges default to
  `gated` (lead inbox first = today's behavior, keeps the coordinator on the edge by default). `tee`/`direct` are
  opt-in per edge, never the default — the operator loosens a gate deliberately, never by omission. This is the
  conservative default the moat wants: legibility and coordinator-gate are the resting state; removing the lead
  from an edge is an explicit act with the M2 circuit breaker already in place.

## 7b. PRE-M0 substrate decision — ✅ RESOLVED 2026-06-11: build NATIVE

Anthropic shipped the first-party **`Workflow` tool** (dynamic, right-sized, ~1,000-subagent runtime
orchestration) on 2026-05-28, which forced the question: **build the shape engine natively (our own DAG runner
over SdkTeammates) OR layer blessed shapes + persistent crew + shape-gate on top of the `Workflow` primitive as
the execution engine?**

**DECISION (Jerome, 2026-06-11): build it NATIVE — our own DAG runner over the existing SdkTeammate/broker
substrate. Do NOT build on the `Workflow` tool.** Rationale: own the substrate. The 4 invariants `Workflow`
lacks — declarative legible shapes, mid-run `send_to` steering, persistent messageable teammates, shape-as-gate —
*are* the product; building on a primitive that lacks them and can't be steered mid-run would compromise all of
them. Owning the runner keeps the shape, the edges, and the gate ours to evolve. Invariant 5 ("bonded to the
Claude Agent SDK") is satisfied by the SdkTeammate substrate we already run; we are not adopting an external
runtime, we are extending our own. M0 builds the native shape engine as drafted above.

## 8. What "ready for repo-react" means here

This draft + the thesis are the **idea input** for a `/repo-reactor:repo-react` heavy-feature run. Suggested
first slice to actually build: **M0** (shape-as-data + shape-gate) — it's the keystone, delivers value alone, and
de-risks everything after it. M1 can fold into the same slice if scope allows; M2+ are separate slices.
