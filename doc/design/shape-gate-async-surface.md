# Shape-gate as an async, resurfaceable, dual-channel surface (design reframe)

Status: **design note, captured 2026-06-12 from live verification with Jerome.** Supersedes the M0 blocking-gate interaction model. Target: M2 (or its own focused slice before M2).

## The problem with the M0 gate (shipped, working, but wrong shape)

M0's `propose_shape` **blocks the lead's turn** on `await_proposal` until a human approves via the dashboard. Live use surfaced three issues:

1. **It freezes the coordinator.** While a proposal is pending, the lead can't chat or do other work — the whole point of the substrate is a *supervising* coordinator, not a blocked one. (Also undercuts the "lead burns ctx on mechanical relay" friction we're trying to reduce.)
2. **Approval is UI-only.** You can only approve by clicking the dashboard. If the operator is on their phone / in chat / away from the dashboard, there's no way to approve. They should be able to **hear the shape over text and approve by telling the coordinator**.
3. **The surface is ephemeral.** The modal vanishes on navigation (switch instances → gone). A blocking gate that you can accidentally dismiss is fragile. It should be **resurfaceable** — pushed with a notification, re-openable, surviving navigation — exactly like the `surface_document` artifact system.

## The reframe: surface, don't block; approve via UI *or* chat; resurface like an artifact

**Preserve the gate invariant** — no crew spawns without an explicit human approval — but **decouple it from blocking** and **give it a chat channel**.

### Tool/flow changes
- **`propose_shape` becomes non-blocking.** It registers the proposal, **surfaces it** (notification + resurfaceable entry, modeled on the artifact registry), and **returns immediately** with `{shape_id, status: "pending"}`. The lead stays free for chat and other work. *(The existing blocking `await_proposal` can remain available for a caller that explicitly wants to block, but it is no longer the primary/default path.)*
- **Notify-on-resolve, don't poll.** When the proposal is resolved (by either channel), the broker drops a **message into the lead's inbox** (same channel as teammate messages) — e.g. `shape <id> approved`. The lead is *notified, not blocked, not polling*. It then calls `instantiate_shape` (which already refuses any non-`approved` shape, so the contract holds).
- **Dual-channel approval:**
  - **UI channel** — Approve/Decline on the dashboard surface (existing `POST /shape-approval/{crew_id}/{shape_id}` + leader→follower proxy).
  - **Chat channel (new)** — the operator tells the coordinator ("approve the shape") and the coordinator calls a new lead tool **`resolve_shape(shape_id, decision)`** → `broker.resolve_proposal(...)`. Works over text, from a phone, with no dashboard open. *(Half-exists today: the coordinator can already hit the HTTP approval route directly; this just makes it a first-class tool.)*

### UI changes — model on the artifact-surface system, not a bespoke modal
- The shape-gate becomes **a surfaced artifact that carries an approve/decline action** — reuse the artifact registry + **unread badge + tray + re-open + notification** machinery (`surface_document` pattern).
- **Resurfaceable + navigation-proof:** a pending gate persists as a badge/tray entry; switching instances doesn't lose it; the operator can re-open it any time. The modal is still the right *visual* when opened — but it's recalled from the tray, not a transient overlay that vanishes.
- Cross-instance aggregation (already shipped) carries over: the tray/badge shows pending gates from any crew.

## What carries over from what we already shipped
- The **modal visual** (M0 + the cross-instance modal slice) is the render — reused, not wasted.
- The **broker proposal state machine** (`pending`→`approved`/`declined`/`timed_out`/`instantiated`, guards, `mark_instantiated`) is intact; only the *blocking* of `propose_shape` and the *surface/notify* wiring change.
- `/shape-approval` route + leader→follower proxy stays as the UI channel.

## Invariants preserved
- **The shape is still the gate** — `instantiate_shape` still refuses anything not human-`approved`; zero spawns without approval.
- **Observable by construction** — resolution still flows through the broker; the notify-the-lead message is on the broker channel.
- **Coordinator-in-the-loop** — *strengthened*: the coordinator now stays free to supervise/chat instead of being frozen, and can itself relay/approve.

## Open questions
- Does `propose_shape` non-blocking break any caller that expected the blocking return? (M0's own validation/tests assume blocking — they'd need updating.)
- Decline/timeout semantics in the non-blocking model (timeout still meaningful? or pending-forever-until-resolved with a manual expire?).
- Should the chat-`resolve_shape` tool be lead-only, or also expose a read (`list_pending_shapes`) so the coordinator can describe pending gates on request ("what's waiting for me?").
