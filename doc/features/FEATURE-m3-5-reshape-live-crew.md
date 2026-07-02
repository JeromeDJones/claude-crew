# Feature: m3-5-reshape-live-crew — Workflow Shape Composition M3.5

**Status:** SHIPPED — 2026-07-01
**Validation:** PASS — stub suite 1708 passed / 0 failed / 41 skipped / 1 xfailed (~249s); both live regression guards green against real crews (`CLAUDE_CREW_LIVE_TESTS=1`).
**Feature tests:** +5 files (reshape-broker-helpers, reshape-crew-gate, reshape-crew-verbs, d0-send-to-unconditional, reshape-docs-staleness) + live-reshape; 4 consumer-test reconciliations.
**Cycles:** all slices PASS cycle 0 (plan-review PASS cycle 0).

---

## Purpose

M3 made shape adaptation *computable* (five pure verbs, pre-instantiation only). M3.5 applies those verbs to an **already-running (instantiated) crew** — mutating the live topology in place: `set_gate` rewires routing, `add_node`/`augment` add teammates/edges *without respawning running teammates*, `drop` kills + cleans overrides, `swap` replaces a slot. claude-crew owns the mechanism; repo-react (policy) owns which reshape to apply.

---

## What shipped

- **`reshape_crew` MCP tool** (`server.py`, +474) — verb discriminator sharing the five `shapes.py` verb objects; five-stage flow (base-resolution → verb-guard → role-resolution → verb.apply → M1.5 gate) then, on approval, per-verb live dispatch. Base must be an INSTANTIATED proposal. Reuses the M1.5 human gate verbatim (`register_proposal`/`await_proposal`). All validator/negative branches (unknown/non-instantiated base, unknown verb, unresolvable role, illegal mutation) rejected pre-gate with no crew mutation.
- **Per-verb live semantics:** `set_gate` → `set_edge_override` (instant); `add_node`/`augment` → spawn (correct neighbors) + `record_topology` + inform already-running edge source via `broker.send` (**no respawn** — D4); `drop` → minus-topology + graceful kill + stale-`_edge_overrides` sweep (D6, both endpoints) + inform survivors; `swap` → spawn replacement + `record_topology(slot→new_id)` **then** graceful-kill old (ordering leaves no dead-slot window — D5). `actions` result record populated per verb.
- **D0 — unconditional `send_to` wiring** (`sdk_teammate.py`, +36) — the `_has_out_edges` spawn-time gate removed; the in-process `send_to` MCP server + tool are wired for **every** SdkTeammate. Enables respawn-free live edge additions. Security unchanged: `broker.authorize_send` (delivery-time topology check) is the boundary — tool presence grants no reach. `tools:[]` yields `['mcp__crew-send__send_to']` (no *pack* tools + the always-present broker-gated framework send_to).
- **3 additive broker helpers** (`broker.py`, +33/−1) — `latest_topology()`, `set_edge_override(from,to,mode)` (generalizes `promote_edge`), `remove_edge_overrides(pairs)` (idempotent). No existing method altered.
- **Docs:** `doc/ARCHITECTURE.md` + `CLAUDE.md` document `reshape_crew`, the D0 contract, and the live-test conventions.

---

## Acceptance tests (20)

- AT 1–3: D0 unconditional send_to (behavioral + `_has_out_edges` deletion-detector + non-regression).
- AT 4, 10–16: `reshape_crew` registration + gate integration + per-validator negative paths (each in isolation).
- AT 5–9: per-verb live dispatch (set_gate, add_node/augment inform, drop + D6 cleanup, swap record-before-kill ordering).
- AT 17–18: **LIVE** regression guards — no-respawn add (same-id teammate reaches a live-added neighbor via a real `send_to` turn on a direct edge) + swap slot-name routing resolves to the replacement.
- AT 19: broker helpers. AT 20: doc staleness detectors.

---

## Design decisions

| Decision | Rationale |
|----------|-----------|
| Live reshape is human-gated (M1.5 reused) | Coordinator-in-the-loop is the moat; killing a running teammate is exactly the high-consequence act the gate exists for. |
| Distinct `reshape_crew` tool (not overloading `adapt_shape`) | Live process side-effects vs pure-data proposal have different semantics; shared verb algebra, distinct application. |
| `send_to` wired unconditionally (D0) | Respawn-free live edge addition. `authorize_send` is the real gate; presence ≠ reach. Contract flip documented + consumer tests reconciled. |
| Additions inform-not-respawn (D4) | Preserves the running teammate's accumulated turn context. |
| Swap: spawn+record **then** kill (D5) | No dead-slot window — slot-name sends resolve to the live replacement throughout. |
| Drop cleans stale `_edge_overrides` (D6) | A stale override keyed by slot would shadow the new topology's declared mode. |

---

## Notable run finding (carried to backlog / RR hardening)

The live tests were the **first** to ever drive a real teammate `send_to` (M2 tested only the broker side at stub level). They initially failed: the tests used the `ShapeEdge` default mode (`gated`), which `_send_routed` routes to the **lead**, not the recipient's inbox — so a peer-delivery assertion timed out despite `send_to` working. Fix: `direct`-mode edges + `general` acting roles. **Lesson (for future repo-react):** any live/behavioral AT asserting teammate→teammate peer delivery must pin an explicit `direct` edge mode, and plan-/feature-review should cross-check the routing mode against `_send_routed`, not just that the seam is wired.

---

## Out of scope

Live tool-set mutation of a running subprocess beyond D0's pre-wiring (SDK bakes allowed-tools at launch); autonomous/ungated reshape (M5); RepoReactor-as-shape (M4); dashboard/UI changes.
