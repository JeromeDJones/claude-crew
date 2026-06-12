# Feature: m1-5-async-shape-gate — Async Shape Gate

**Status**: Complete
**Created**: 2026-06-12
**Slug**: m1-5-async-shape-gate
**Spec**: `.rr/specs/m1-5-async-shape-gate.md`

---

## Problem Statement

M0's shape-gate enforced its core invariant mechanically but was operationally hostile:

1. **Blocking**: `propose_shape` froze the lead's entire turn until a human clicked Approve/Decline on the dashboard — defeating the substrate's premise (lead supervises while crew works).
2. **UI-only approval**: no way to approve from a phone, over text, or away from the dashboard.
3. **Ephemeral gate surface**: the M0 modal vanished on instance-switch; a pending gate could be accidentally dismissed and never recovered.

M1.5 fixes the interaction model without changing the visual or weakening the core spawn-gate invariant.

---

## What Shipped

### `claude_crew/server.py` (+95/-17)

- **`propose_shape(shape, wait=False, ...)`** — **non-blocking by default**: parse → register → surface → return `{ok, shape_id, status:"pending", shape}` immediately. The lead stays free to supervise and chat while a gate is open. `wait=True` retains the M0 blocking path via `await_proposal` (600s default timeout).
- **`resolve_shape(shape_id, decision)`** — NEW MCP tool (chat-channel approval): validates `decision ∈ {"approve","decline"}`, guards unknown-id / non-pending / invalid decision, calls `broker.resolve_proposal`, returns `{ok:True, shape_id, status}` or `{ok:False, error}`.
- **`list_pending_shapes()`** — NEW MCP tool (read): returns `{ok:True, pending:[{shape_id, name, crew_id, mermaid, summary}]}`; empty list when none pending.
- **Tool count: 14 → 16.**

### `claude_crew/broker.py` (+83 insertions)

- **`resolve_proposal(shape_id, decision)`** gains a **lead-inbox notify** (single choke point): after flipping status and notifying `_proposal_condition`, sends `{type:"shape_resolved", shape_id, status}` to `LEAD_ID` via the lead-message channel (`send` → `_lead_message_condition`). Both the chat channel (`resolve_shape`) and the UI channel (`POST /shape-approval`) route through `resolve_proposal`, so both inherit the notify for free. No resolution path can complete without waking the lead's `get_messages` loop.

### `claude_crew/ui_server.py` (+16 insertions)

- **`_build_local_instance`** — `shape_proposals` entries gain `name` (`p.shape.name`) and `summary` (`p.shape.description`) so the dashboard tray has a human-readable title per proposal.

### `claude_crew/ui/dashboard.html` (+43/-19)

- **`MCTopBar`** — gains a `pending-gate-pill` badge: count derived from a cross-instance `flatMap` over all `shape_proposals` filtered to `status === "pending"`. Badge renders for any pending proposal across all instances; persists across instance-switch and on modal close.
- **`ShapeGatePanel`** — promoted from auto-open transient modal to **controlled component**: `open`/`onClose` from parent `gateOpen` state. Pill click → open; closing sets `gateOpen=false` but leaves badge; badge clears only on resolution.
- Fixes the M0 bug: gate survives instance-switch.
- `MCTopBar` badge uses cross-instance `flatMap` (all crews' `shape_proposals`), so a follower's pending gate surfaces on the leader dashboard.

### Tests

| File | Change | Tests |
|------|--------|-------|
| `tests/test_shape_gate.py` | 14 pre-existing blocking-contract tests migrated to non-blocking; 10 new tests added | 24 total |
| `tests/test_shape_broker.py` | 4 new tests: AT3 approve, decline, no-notify-before-resolve, single-notify-on-double-resolve | 35 total |
| `tests/test_shape_dashboard.py` | 1 new test: `test_proposal_carries_name_and_summary` | 25 total |
| `tests/dashboard/test_shape_resurface.py` | NEW file — 5 headless-Chromium Playwright tests | 5 new |
| `tests/test_shape_render.py` | Minimal update: add pill-click step before `.shape-gate-panel` in AT13/AT14 (required by controlled-component refactor) | existing |

---

## Acceptance Tests

| AT | Description | Result |
|----|-------------|--------|
| AT1 | `propose_shape(wait=False)` returns `{ok, shape_id, status:"pending"}` in < 1ms, no block | ✅ PASS (0.52ms live) |
| AT2 | `resolve_shape` approve → `instantiate_shape` spawns; decline → zero spawn; 3 sad-path `ok:False` | ✅ PASS |
| AT3 | `resolve_proposal` emits `{type:"shape_resolved", ...}` to lead inbox; no envelope before resolve; single notify on double-resolve attempt | ✅ PASS |
| AT4 | `/api/state` proposal entry carries `name`, `summary`, `status`, `crew_id`, `mermaid` | ✅ PASS |
| AT5 | Badge renders for pending proposal; modal opens from pill; persists across instance-switch; close keeps badge; resolves clears badge | ✅ PASS (5 Playwright) |
| AT6 | `list_pending_shapes` returns populated list and empty list correctly; resolved proposals absent | ✅ PASS |

**Full suite:** 1461 passed, 34 skipped, 1 xfailed, 52 warnings in 183.91s (exit 0). Zero failures. Live UX verified by Jerome 2026-06-12: APPROVED.

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| Non-blocking by default (`wait=False`); blocking opt-in (`wait=True`) | Decouples approval from lead blocking; back-compat for any `wait=True` caller |
| Single choke-point notify in `broker.resolve_proposal` | Both resolution channels (chat + UI) route through it — observable-by-construction invariant; no per-call-site notify code |
| Minimal notify payload `{type, shape_id, status}` | Lead needs only which proposal resolved and how to call `instantiate_shape`; richer payloads invite coupling |
| No auto-timeout for non-blocking pending gates | Gate is resurfaceable; auto-expire would silently drop a gate the operator never saw. `await_proposal` timeout retained only for `wait=True` |
| Trust-enforced (not mechanical) human-in-the-loop | `resolve_shape` on the lead surface means the coordinator *can* resolve a gate. Intentional — bridge to M1's blessed/trusted-shape auto-approval |
| Resurfaceable gate reuses artifact-tray pattern | `MCTopBar` badge + controlled `ShapeGatePanel`; proven machinery, no new persistence mechanism |
| `resolve_shape` pre-checks (guard duplication) | MCP contract requires `ok:False` returns, not raises; pre-check sees consistent status (no two-channel race under asyncio single-suspension); intentional |

**The one un-softened mechanical invariant:** `instantiate_shape` still refuses every non-`approved` proposal. Nothing spawns without a human (or stubbed) approval.

---

## Design Notes

- **Two unrelated "gate" concepts** — the *shape-gate* (human-approval checkpoint: M0/M1.5) and *gated edge* (per-edge routing mode: M2) share the word "gate" but are different mechanisms.
- **M0 blocking tests migrated** — all 14 pre-existing tests that used the blocking create_task/poll pattern were updated to use the non-blocking contract. `test_timed_out_proposal_refused` exercises the retained `wait=True` path.
- **Cross-instance badge** — the `pendingGateCount` is derived from all instances' `shape_proposals`, independent of `activeId`. A follower's gate surfaces on the leader without selecting that instance.

---

## Deferred / Out of Scope

- Edge routing, scoped `send_to`, circuit breaker — M2.
- Blessed shape library + auto-approval policy — M1 (after M2).
- In-gate shape editing — M3 adaptation algebra.
- `shape_to_mermaid` `\n` → `<br>` label fix — tracked in BACKLOG (pre-existing M0 defect, fast-follow).
