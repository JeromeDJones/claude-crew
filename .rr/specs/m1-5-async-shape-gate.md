# Spec: m1-5-async-shape-gate

## Problem

M0 shipped a shape-gate that enforced its core invariant — no crew spawns without a human approval — **mechanically**: `propose_shape` blocks the lead's turn on `broker.await_proposal`, and the *only* way to resolve is a human physically clicking Approve/Decline on the dashboard (`POST /shape-approval`). Because the LLM coordinator cannot click a button, that guarantee is one the model literally cannot fake. M0's interaction shape, though, is wrong in three ways: (1) Blocking freezes the supervising coordinator — while a proposal is pending the lead cannot chat or supervise, which defeats the substrate's premise and worsens the "lead burns context on relay" friction. (2) Approval is UI-only — there is no way to approve from a phone, over text, or away from the dashboard. (3) The gate surface is ephemeral — the M0 modal is view-tied and vanishes on instance-switch, so a pending gate can be accidentally dismissed and never recovered. M1.5 makes `propose_shape` **non-blocking**, adds a **chat-channel approval** path (a new `resolve_shape` lead tool plus a `list_pending_shapes` read), **notifies the lead via its inbox** when a proposal resolves through either channel (no polling, no freezing), and makes the dashboard gate **resurfaceable** like a surfaced artifact (badge/tray entry that survives navigation, re-opens the existing modal, and clears only on resolution). Putting `resolve_shape` on the lead surface **deliberately softens** the human-in-the-loop guarantee from *mechanically un-fakeable* (M0) to *trust-enforced* (the coordinator now technically *can* resolve a gate it proposed) — an intentional trade that is the bridge to M1's trusted/blessed-shape auto-approval (see Design Decisions). The one genuinely-unchanged mechanical fact is that `instantiate_shape` still refuses any non-`approved` proposal.

## Architecture Overview

The change touches four modules, each a thin layer over the M0 proposal state machine, which is left intact (`register_proposal` / `resolve_proposal` / `await_proposal` / `get_proposal`, states `pending → approved/declined/timed_out/instantiated`).

- `claude_crew/server.py` — `propose_shape` stops awaiting the resolution and returns immediately with `{ok, shape_id, status:"pending", shape}`; a non-default `wait=True` flag retains the M0 blocking path via `await_proposal`. Two new MCP tools: `resolve_shape(shape_id, decision)` (chat-channel approve/decline) and `list_pending_shapes()` (read). Tool count 14 → 16.
- `claude_crew/broker.py` — `resolve_proposal` becomes the single resolution choke point: in addition to flipping status and notifying `_proposal_condition`, it drops a `{type:"shape_resolved", shape_id, status}` envelope into the lead inbox via the existing lead-message channel (`send` to `LEAD_ID` → `_lead_message_condition`). Because both the chat channel (`resolve_shape`) and the UI channel (`/shape-approval`) call `resolve_proposal`, both inherit the notify for free.
- `claude_crew/ui_server.py` — `_build_local_instance` already emits `shape_proposals` carrying `crew_id`, `status`, `adaptation_diff`, `mermaid`; add a human-readable `name` (and `summary`) field derived from `proposal.shape` so the tray entry has a title. `/shape-approval/{crew_id}/{shape_id}` route + leader→follower proxy unchanged.
- `claude_crew/ui/dashboard.html` — promote the M0 shape-gate from a transient `OverlayPanel mode="modal"` (re-asserted on each new pending set) to a **resurfaceable** surface modeled on the artifact tray: a pending gate shows an unread badge/tray entry in `MCTopBar` (mirroring the artifact unread pill), the modal opens from the tray, switching instances does not lose it, closing leaves the badge, and it clears only when the proposal resolves. The modal visual and the cross-instance `flatMap` aggregation are reused as-is.

### Call-site survey

`broker.resolve_proposal` is the one helper whose call-sites diverge in shape — the notify must fire from all of them, which is why the notify lives inside `resolve_proposal` rather than at each call-site.

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| Chat channel | `server.py::resolve_shape` (new) | `await broker.resolve_proposal(shape_id, decision)` | MCP tool; operator-told-coordinator path |
| UI channel | `ui_server.py::POST /shape-approval` (~line 873) | `self._broker.resolve_proposal(shape_id, decision)` | Route path carries `crew_id` for instance routing only (`/shape-approval/{crew_id}/{shape_id}`); `crew_id` is **not** a `resolve_proposal` argument. Local resolve when `crew_id == own`, else proxy to follower. M0 route unchanged. |
| Opt-in blocking caller | `server.py::propose_shape(wait=True)` | `await broker.await_proposal(...)` (not resolve) | Retained non-default path |

Resolution: **put the lead-notify inside `broker.resolve_proposal`** (single choke point). Both resolution call-sites inherit identical notify behavior; no per-call-site notify code, no path that resolves without notifying.

## Data / API Contracts

```
# server.py — new + changed MCP tools (lead surface 14 → 16)

propose_shape(shape: dict, adaptation_diff: str | None = None,
              wait: bool = False, timeout_seconds: float = 600) -> dict
    # DEFAULT (wait=False): parse → register_proposal → surface → return immediately.
    #   returns {ok: True, shape_id: str, status: "pending", shape: {name, description, nodes}}
    #   on parse failure: {ok: False, stage: "parse", error: str}
    # OPT-IN (wait=True): M0 behavior — await_proposal(timeout_seconds), return resolved status.

resolve_shape(shape_id: str, decision: str) -> dict   # NEW — chat channel
    # decision ∈ {"approve","decline"} → broker.resolve_proposal(shape_id, decision)
    #   ok:    {ok: True, shape_id, status: "approved"|"declined"}
    #   unknown shape_id:        {ok: False, error: "unknown shape_id: <id>"}
    #   already-resolved/pending-guard fail: {ok: False, error: "<id> is not pending (status=<s>)"}
    #   invalid decision:        {ok: False, error: "decision must be 'approve' or 'decline'"}

list_pending_shapes() -> dict                          # NEW — read
    # returns {ok: True, pending: [{shape_id, name, crew_id, mermaid, summary}, ...]}
    # empty list when no pending proposals.

# broker.py — resolve_proposal gains a lead-notify side effect (signature unchanged)
resolve_proposal(shape_id, decision)
    # existing: pending-only guard → set status approved/declined → notify _proposal_condition
    # ADDED:    send(Envelope(recipient=LEAD_ID,
    #                         payload={"type": "shape_resolved", "shape_id": shape_id,
    #                                  "status": "approved"|"declined"}))
    #           → notifies _lead_message_condition so the lead's get_messages loop wakes.

# ui_server.py — _build_local_instance shape_proposals entry gains `name` (+ `summary`)
{ "shape_id", "crew_id", "status", "adaptation_diff", "mermaid",
  "name": proposal.shape.name, "summary": proposal.shape.description }
```

## Design Decisions

- **`propose_shape` is non-blocking by default; `wait=True` retains the M0 blocking path.** — *Rationale:* decouples approval from blocking so the coordinator stays free to supervise/chat, while preserving a back-compat blocking caller. — *Carried into:* `server.py::propose_shape` `wait` parameter + AT1 (returns `status:"pending"` without waiting) + AT2.
- **The human-in-the-loop guarantee shifts from *mechanical* (M0) to *trust-enforced* (M1.5) — a deliberate trade.** — *Rationale:* M0's UI-only approval was un-fakeable by the LLM (a model cannot click a dashboard button); putting `resolve_shape` on the lead MCP surface means the coordinator *can* now resolve a gate, including one it proposed itself. This softening is intentional and desired: it is the mechanism that lets a coordinator auto-approve a well-known/trusted shape once a pattern is proven — the bridge to M1's blessed-shape library. The guarantee now holds *as long as the coordinator only resolves on genuine human instruction*, not because the model is mechanically barred. The only mechanical invariant that remains un-softened is `instantiate_shape`'s refusal of any non-`approved` proposal (zero spawns from an unresolved gate). — *Carried into:* `server.py::resolve_shape` on the lead surface + AT2; Edge Cases (`instantiate_shape` refusal) + AT2.
- **The lead-notify lives inside `broker.resolve_proposal`, not at each call-site.** — *Rationale:* both the chat channel and the UI channel resolve through `resolve_proposal`; a single choke point guarantees no resolution path can complete without notifying the lead (observable-by-construction invariant). — *Carried into:* `broker.resolve_proposal` send-to-LEAD + AT3.
- **Notify payload is minimal: `{type:"shape_resolved", shape_id, status}`.** — *Rationale:* the lead only needs to learn *which* proposal resolved and *how* so it can call `instantiate_shape`; richer payloads invite coupling. — *Carried into:* AT3 assertion on `get_messages` payload.
- **No auto-timeout in the non-blocking path; the gate lives until explicitly resolved.** — *Rationale:* the gate is resurfaceable, so an operator can always decline later; an auto-expire would silently drop a gate the operator never saw. `await_proposal`'s timeout is retained *only* for the opt-in `wait=True` path. — *Carried into:* `propose_shape(wait=False)` never calls `await_proposal`; Assumption + Open Question resolution.
- **`resolve_shape` is the chat-channel approval tool; the coordinator describes the shape in its own message (no auto-surfaced one-liner).** — *Rationale:* keeps the tool surface minimal and lets the coordinator phrase the description for the operator; `list_pending_shapes` supplies the mermaid/summary it needs to do so. — *Carried into:* `server.py::resolve_shape` + AT2; `list_pending_shapes` + AT6.
- **`list_pending_shapes` is built (not deferred).** — *Rationale:* it is what makes phone/text approval ergonomic — the coordinator can answer "what's waiting for me?" and describe the DAG over text before the operator approves. — *Carried into:* `server.py::list_pending_shapes` + AT6; tool count 16.
- **The resurfaceable gate reuses the artifact-tray/unread-pill pattern; the M0 modal is reused as the visual.** — *Rationale:* the artifact surface already solves navigation-persistence + unread badge + re-open; mirroring it (rather than a bespoke modal lifecycle) fixes the "vanishes on instance-switch" bug with proven machinery. — *Carried into:* `dashboard.html` MCTopBar badge + tray entry + recallable modal; AT5.
- **`shape_proposals` emission gains a `name`/`summary` field.** — *Rationale:* the tray entry needs a human title; the M0 payload carries only `mermaid`/`status`/`crew_id`. — *Carried into:* `ui_server.py::_build_local_instance` + AT4/AT5.

## Edge Cases

- **`propose_shape` returns while the proposal is still pending** — the assertion of non-blocking: immediately after the call returns, `broker.get_proposal(shape_id).status == "pending"`.
- **Lead never resolves a pending gate** — no auto-timeout in the non-blocking path; the gate stays pending and resurfaceable indefinitely until an operator approves/declines via either channel.
- **`resolve_shape` on an unknown `shape_id`** — broker raises `KeyError`; tool returns `{ok: False, error: ...}` (does not raise).
- **`resolve_shape` on an already-resolved proposal** (approved / declined / instantiated) — the M0 pending-only guard raises `ValueError`; tool returns `{ok: False, error: "<id> is not pending"}`. No second notify is emitted (the guard fires before the notify).
- **`resolve_shape` with an invalid `decision`** (not `"approve"`/`"decline"`) — rejected with `{ok: False, error: ...}`; no state change, no notify.
- **Two channels race to resolve the same gate** (operator clicks Approve on dashboard while also telling the coordinator) — whichever `resolve_proposal` runs first wins and emits exactly one `shape_resolved` notify; the second hits the pending-only guard and returns an error / no-op. The lead sees one notification, not two.
- **Multiple pending gates at once** — each has a distinct `shape_id`; the tray shows all of them; resolving one drops only that tray entry and emits one notify for that `shape_id`.
- **`instantiate_shape` still refuses any non-`approved` proposal** — declined / pending / timed_out / already-instantiated all return `ok:False`; an unresolved gate yields zero spawns. This mechanical refusal is genuinely unchanged from M0. Note the scope: it guarantees no spawn from an *unresolved* proposal — it does **not** (and in M1.5 cannot) guarantee that the `approved` status came from a human rather than the coordinator's own `resolve_shape` call; that is now a trust-enforced property, per the Design Decision on the mechanical→trust shift.
- **`list_pending_shapes` when none are pending** — returns `{ok: True, pending: []}` (empty, not an error).
- **No `shape_resolved` message before resolution** — `get_messages` for the lead contains no `shape_resolved` envelope for that `shape_id` while the proposal is pending (AT3 sad path).

**If this feature affects displayed data, answer these:**
- **UI when data is absent (no pending gates):** the shape-gate tray/badge shows no unread count and no entry; the dashboard renders normally with no modal.
- **UI when expired/capped:** there is no expiry in the non-blocking path, so a pending gate never appears "expired"; once resolved, its tray entry clears immediately.
- **Zero vs missing:** zero pending gates = no badge (unread count omitted/0); a pending gate that has been opened-but-not-resolved still shows in the tray (read, no unread dot) until resolved — distinct from "missing" (cleared on resolution).

**If this feature retires, expires, or caps data, answer these:**
- **Which consumers read `shape_proposals`?** `ui_server.py::_build_local_instance` (emits it on `/api/state`); `dashboard.html` `ShapeGatePanel` / new tray (renders it); the leader's `_build_state` aggregates followers' instances and `flatMap`s their `shape_proposals`. (Read from the code, not guessed — confirmed in grounding.)
- **What does each consumer show on a resolved record?** A resolved proposal leaves the pending set, so the tray/badge no longer renders it; the modal, if open on that gate, closes/clears.
- **Filtering/aggregation that assumes "live":** the dashboard filters `shape_proposals` by `status === 'pending'` before rendering; the leader aggregation `flatMap`s across instances — both already filter on `pending`, so resolved records are naturally excluded.

## Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions | Rollback |
|---|---|---|---|---|
| `propose_shape` → broker registry | shape parses | parse error → `{ok:False, stage:"parse"}`, nothing registered | proposal stored `pending`; tool returns `shape_id` without blocking | n/a (no spawn yet) |
| resolve (either channel) → lead inbox | proposal is `pending` | non-pending → guard error, no notify | status flipped; one `shape_resolved` envelope in lead inbox; `_lead_message_condition` notified | n/a (idempotent guard prevents double-resolve) |
| lead `get_messages` → `instantiate_shape` | `shape_resolved` seen with `status:"approved"` | declined/pending → `instantiate_shape` returns `ok:False`, zero spawns | approved crew spawned; `Topology` recorded; proposal → `instantiated` | pre-flight role resolution is all-or-nothing (M0) |

## Acceptance Tests

1. **Non-blocking propose.** Given a server with a valid 2-node shape, when the lead calls `propose_shape({shape})` (default `wait=False`), then the call returns promptly with `{ok:True, status:"pending", shape_id}` **without** awaiting a resolution, and `broker.get_proposal(shape_id).status == "pending"` immediately after the call returns (the proposal is still pending in the broker). Construction: register the shape via the `propose_shape` MCP tool against a stub-mode server.
2. **Chat-channel approval.** Given a pending proposal created by `propose_shape` (as in AT1), when the lead calls `resolve_shape(shape_id, "approve")`, then `broker.get_proposal(shape_id).status == "approved"` and a subsequent `instantiate_shape(shape_id)` spawns the crew (`ok:True`, teammates registered). When instead `resolve_shape(shape_id, "decline")` is called, the status is `"declined"` and `instantiate_shape` returns `ok:False` (zero spawns). Sad: `resolve_shape` on an unknown `shape_id` returns `{ok:False, error}`; `resolve_shape` on an already-resolved `shape_id` returns `{ok:False, error}` (pending-only guard); an invalid `decision` returns `{ok:False, error}`.
3. **Notify-on-resolve.** Given a pending proposal and a lead consuming `broker.get_messages(LEAD_ID, since_seq)`, when the proposal is resolved through *either* channel (call `broker.resolve_proposal(shape_id, "approve")` directly to model both), then a `{type:"shape_resolved", shape_id, status:"approved"}` envelope appears in the lead's `get_messages` stream for that `shape_id`. Sad: before any resolution, the lead's `get_messages` stream contains **no** `shape_resolved` envelope for that `shape_id`.
4. **UI channel still works (carry over M0 AT11/AT12).** Single-instance: `GET /api/state` includes the pending proposal carrying `crew_id`, `status`, a non-empty `mermaid` string, and the new human-readable `name`; `POST /shape-approval/{own_crew_id}/{shape_id}` with `{"decision":"approve"}` returns ok and the broker proposal transitions to `approved`; bad path params → 400, invalid decision → 400, unknown `shape_id` → 404. Multi-instance: the leader proxies `POST /shape-approval/{follower_crew_id}/{shape_id}` to the follower's own endpoint and the follower's broker resolves to `approved`; a `crew_id` absent from the registry → 404. Construction: build a `UIServer` over a `Broker` with one registered pending proposal (mirror `tests/test_shape_dashboard.py` `_make_shape`/`_make_ui`).
5. **Resurfaceable gate (Playwright, headless Chromium).** Given a live dashboard server with a pending proposal in the broker, when the operator loads the dashboard, then a pending-gate unread badge/tray entry renders in the top bar; opening it shows the modal with the DAG (mermaid); **switching the active instance does not lose the pending gate** (badge/tray entry persists across navigation — the M0 bug fix); closing the modal leaves the badge; and resolving the proposal (`POST /shape-approval/{crew_id}/{shape_id}`) clears the tray entry on the next state poll. Cross-instance: a follower's pending gate surfaces on the leader's tray (carry over the M0 cross-instance modal aggregation). Construction: stand up a live `UIServer` (mirror `tests/test_dashboard_render.py` live-server fixture), seed one pending proposal, drive with `page.goto(live_server_url)`.
6. **`list_pending_shapes`.** Given two pending proposals, when the lead calls `list_pending_shapes()`, then it returns `{ok:True, pending:[...]}` with one entry per pending proposal, each carrying enough to describe over text (`shape_id`, `name`, `crew_id`, `mermaid`/`summary`). Given no pending proposals, it returns `{ok:True, pending:[]}` (empty).

## Test Command

Prerequisites: run `uv sync` once to install the dependency groups (all imports — `httpx`, `pytest`, `pytest-asyncio`, `pytest-playwright`, `uvicorn`, `mcp` — are already declared in `pyproject.toml`'s `dev` group). AT5 drives a headless Chromium via `pytest-playwright`, which requires the browser binary: run `uv run playwright install chromium` once before the suite. The two `test_shutdown_signals` failures are a pre-existing baseline flake unrelated to this feature.

```bash
uv sync && uv run playwright install chromium && uv run pytest
```

## Out of Scope

- Edge routing, scoped teammate `send_to`, neighbor injection, circuit breaker — that is M2.
- Blessed shape library and right-sizing router — that is M1 (deferred). M1.5 enables it (the trust-enforced gate makes coordinator auto-approval *possible*) but does not build the trusted-shape registry or any auto-approval policy.
- Adaptation algebra (M3), RR re-author (M4), autonomy (M5).
- Editing the proposed shape before approval (`edited_shape`) — M0 is approve/decline only and M1.5 keeps that.
- Changing the `/shape-approval` route or its leader→follower proxy — reused unchanged.
- Authoring `doc/ARCHITECTURE.md` updates for the new tools/contracts — handled by the RR documenter (doc-sync) phase, not an implementation task here.

## Assumptions

- **No auto-timeout for non-blocking pending gates.** — *Default:* a pending gate lives until explicitly resolved; `await_proposal`'s timeout applies only to the opt-in `wait=True` path. — *Rationale:* the gate is resurfaceable, so an operator can always decline; an auto-expire risks silently dropping a gate the operator never saw (brief §9, leaning answer).
- **The coordinator describes the shape in its own chat message; `resolve_shape` does not auto-surface a one-line description.** — *Default:* chat description is the coordinator's responsibility, fed by `list_pending_shapes`. — *Rationale:* keeps the tool surface minimal (brief §9, leaning answer).
- **Notify payload is exactly `{type, shape_id, status}`.** — *Default:* minimal envelope. — *Rationale:* the lead only needs which/how to call `instantiate_shape` (brief §9, leaning answer).
- **`list_pending_shapes` is in scope (built, not deferred).** — *Default:* build it. — *Rationale:* the brief marks it "recommended — it's what makes phone/text approval ergonomic," and AT6 depends on it.
- **Tool count becomes 16** (14 + `resolve_shape` + `list_pending_shapes`). — *Default:* register both. — *Rationale:* both tools are in scope; brief says "14→15(+)" with `list_pending_shapes` as the optional `+`, which this spec includes.
- **The resurfaceable gate reuses the artifact unread/tray pattern rather than introducing a new persistence mechanism.** — *Default:* mirror `MCTopBar` artifact pill + `ArtifactTray`. — *Rationale:* brief §3c/§3d explicitly direct reuse of that machinery.
- **`shape_proposals` gains `name` (and `summary`) only; no other field changes.** — *Default:* derive both from `proposal.shape` (`name`, `description`). — *Rationale:* minimal change to give the tray a title without reshaping the existing payload.

## Open Questions

- (none) — the three brief §9 questions are resolved above as Assumptions with the brief's leaning defaults (no auto-timeout; coordinator-describes; minimal notify payload). None blocks implementation.

## Validation

After feature-review PASS, exercise the user-visible promised outcome end to end: the coordinator stays free after proposing, the operator can approve over chat *or* UI, the lead is notified without polling, and the gate survives navigation. The automated proof is the full suite (which includes the non-blocking propose, dual-channel resolve, notify-on-resolve, and the Playwright resurfaceable-gate AT). Run from the worktree root (prerequisites as in ## Test Command — `uv sync` and `uv run playwright install chromium`):

```bash
uv sync && uv run playwright install chromium && uv run pytest
```

Manual UX confirmation (human-judged, pass/fail): with two dashboard instances running, call `propose_shape` on a follower; confirm (1) the call returns immediately and the coordinator can still send a chat message; (2) a pending-gate badge appears on the **leader's** top bar; (3) switching the active instance does not lose the badge; (4) approving via `resolve_shape` in chat clears the badge on both instances and drops a `shape_resolved` message in the lead's stream; (5) `instantiate_shape` then spawns the crew. PASS requires all five.

## Task Breakout

```yaml
tasks:
  - name: broker-notify-on-resolve
    description: |
      In claude_crew/broker.py, make resolve_proposal emit a lead-inbox
      notification on success: after the existing pending-only guard flips
      status and notifies _proposal_condition, send an Envelope to LEAD_ID
      with payload {"type":"shape_resolved","shape_id":...,"status":...} via
      the existing lead-message channel (send → _lead_message_condition), so
      the lead's get_messages loop wakes without polling. Exactly one notify
      per resolution; the guard must fire before the notify so already-resolved
      proposals produce no second message. Add/extend tests in
      tests/test_shape_broker.py for the happy path (message appears after
      resolve) and the sad path (no shape_resolved message before resolution).
    dependsOn: []
    acceptanceTests: [3]
    taskTouches:
      - "claude_crew/broker.py"
      - "tests/test_shape_broker.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_broker.py
  - name: server-async-tools
    description: |
      In claude_crew/server.py, make propose_shape non-blocking by default:
      parse → register_proposal → surface → return {ok,shape_id,status:"pending",
      shape} immediately, with a non-default wait=True flag retaining the M0
      await_proposal blocking path. Add MCP tool resolve_shape(shape_id,
      decision) calling broker.resolve_proposal (approve/decline), returning
      ok:False with an error message on unknown id / already-resolved
      (pending-only guard) / invalid decision instead of raising. Add MCP tool
      list_pending_shapes() returning {ok,pending:[{shape_id,name,crew_id,
      mermaid,summary}]} (empty when none). Tool count 14→16. Migrate the M0
      blocking-contract tests in tests/test_shape_gate.py (the create_task /
      poll-then-resolve pattern) to the non-blocking contract: propose returns
      pending; resolve; then assert.
    dependsOn: []
    acceptanceTests: [1, 2, 6]
    taskTouches:
      - "claude_crew/server.py"
      - "tests/test_shape_gate.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_gate.py
  - name: ui-server-shape-surface
    description: |
      In claude_crew/ui_server.py, extend the shape_proposals entry emitted by
      _build_local_instance to carry a human-readable name (proposal.shape.name)
      and summary (proposal.shape.description) alongside the existing shape_id,
      crew_id, status, adaptation_diff, and mermaid, so the dashboard tray has a
      title. Leave the POST /shape-approval/{crew_id}/{shape_id} route and its
      leader→follower proxy unchanged. Update tests in
      tests/test_shape_dashboard.py to assert the new name field on /api/state
      and to keep AT11/AT12 (single + multi-instance approval) green.
    dependsOn: []
    acceptanceTests: [4]
    taskTouches:
      - "claude_crew/ui_server.py"
      - "tests/test_shape_dashboard.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_shape_dashboard.py
  - name: dashboard-resurfaceable-gate
    description: |
      In claude_crew/ui/dashboard.html, promote the shape-gate from a transient
      modal to a resurfaceable surface modeled on the artifact tray: render a
      pending-gate unread badge/tray entry in MCTopBar (mirror the artifact
      unread pill), open the existing modal (OverlayPanel mode="modal", reused
      visual) from the tray, persist the entry across instance-switch, leave the
      badge when the modal is closed, and clear the entry only when the proposal
      resolves. Reuse the cross-instance flatMap aggregation so a follower's
      pending gate surfaces on the leader. Add a headless-Chromium Playwright
      test (tests/dashboard/test_shape_resurface.py, @pytest.mark.dashboard,
      live-server fixture mirroring tests/test_dashboard_render.py) covering
      badge render, open-modal-shows-DAG, persists-across-instance-switch,
      and clears-on-resolve.
    dependsOn: [ui-server-shape-surface]
    acceptanceTests: [5]
    taskTouches:
      - "claude_crew/ui/dashboard.html"
      - "tests/dashboard/test_shape_resurface.py"
      - "tests/dashboard/**"
      - "tests/test_dashboard_render.py"
    implementationKind: behavior-change
    testCommand: |
      uv run playwright install chromium && uv run pytest tests/dashboard/test_shape_resurface.py
```

## Design Notes

- The notify-in-`resolve_proposal` choke-point design means the UI channel (`/shape-approval` → `resolve_proposal`) gets the lead-inbox notification for free; no change to `ui_server.py`'s approval route is needed for AT3 to hold via the UI channel. AT3 models "either channel" by calling `broker.resolve_proposal` directly (the common path both channels traverse).
- `broker-notify-on-resolve`, `server-async-tools`, and `ui-server-shape-surface` touch disjoint files and can run in parallel; only `dashboard-resurfaceable-gate` depends on `ui-server-shape-surface` (it consumes the new `name` field in the tray). The three independent tasks together form the async loop, but each is independently testable at its own layer.
- `doc/ARCHITECTURE.md` currently documents `propose_shape` as "blocks on `await_proposal`" and lists 14 tools; that prose is now stale. Updating it is the RR documenter's doc-sync responsibility, not an implementation task — flagged here so the retrospecting phase catches it.
- Grounding line references (current master, for the implementor): `server.py` `propose_shape` ~736–786, `instantiate_shape` ~789, `surface_document` ~649; `broker.py` `register_proposal` ~1087, `resolve_proposal` ~1133 (pending guard ~1149), `await_proposal` ~1107, `send`/LEAD-notify ~776–820, `get_messages` ~860, `_lead_message_condition` ~206; `ui_server.py` `_build_local_instance` ~209 (shape_proposals ~445), `/shape-approval` ~823, proxy ~884; `dashboard.html` `ShapeGatePanel` ~2306, `OverlayPanel` ~1106, `ArtifactTray` ~1207, `MCTopBar` ~1381.
```
