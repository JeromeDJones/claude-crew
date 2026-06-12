# Backlog Candidates: workflow-shape-composition-m0

**Feature**: workflow-shape-composition-m0
**Cycle**: 0
**Date**: 2026-06-11
**Workflow-retro enabled**: false

> *Workflow-retro was disabled for this run. Process observations from the coordinator are surfaced directly as BACKLOG candidates below (tagged `tooling` / `skill` / `coordinator` by layer). Code findings are pre-routed from the slice and feature review reports.*

---

## Deferred Code Findings (pre-routed from slice/feature reviews)

Six findings surfaced by the five slice-reviews and the feature-review. All are **Low** or **Info** — none blocked the PASS verdicts. Routed to the backlog per the feature-review "cracks-fell-through" section.

### [Low] `resolve_proposal` has no source-state guard

- **What**: `broker.resolve_proposal(shape_id, decision)` enforces only the *value* of `decision` (`"approve"`/`"decline"`), not that the current proposal status is `"pending"`. It will silently flip `timed_out`→`approved`, `declined`→`approved`, or even `approved`→`declined`. In practice the exposure is small — `propose_shape` has already returned `timed_out` to the lead before a late approval arrives, so the lead re-proposes rather than instantiates — but a `pending`-only precondition would harden the gate.
- **Where**: `claude_crew/broker.py::Broker.resolve_proposal`
- **Why it matters**: Loose state machine at the one hard approval gate. A future fast-path, test shortcut, or concurrent approval attempt could mis-transition without error.
- **Suggested action**: Add: `if proposal.status != "pending": raise ValueError(f"Cannot resolve proposal in state {proposal.status!r}")`. Add a test for the attempted double-resolve path. XS change.

### [Info] Pre-flight ↔ `_resolve_role` logic duplication

- **What**: `server.instantiate_shape` re-implements the `*:role` suffix-promotion rule (exact match OR unique key ending `f":{role}"`) rather than sharing it with `factories._resolve_role`. Pre-flight is verified strictly more conservative than `_resolve_role` — it only refuses roles the factory would degenerate-spawn, never admits one the factory would reject. Risk: the two copies can drift if `_resolve_role` changes (e.g., case-insensitive matching, glob support).
- **Where**: `claude_crew/server.py::instantiate_shape` pre-flight block; `claude_crew/factories.py::_resolve_role` (nested closure, line ~380)
- **Why it matters**: Two implementations of the same promotion rule. A future `_resolve_role` change propagates to spawn-time resolution but not to pre-flight, producing a divergence window where pre-flight refuses a role that spawn-time would accept (or vice versa).
- **Suggested action**: Hoist `_resolve_role` to module scope (currently a nested closure — moving it out is safe), or expose a `factory.resolve_role(role)` accessor alongside `factory.known_roles`. Pre-flight calls the shared helper. Medium refactor; safe to defer until M2 when routing logic expands.

### [Info] `Topology` frozen-but-mutable `slot_to_teammate` dict

- **What**: `Topology` is `@dataclass(frozen=True)` but its `slot_to_teammate` field is a plain `dict`. `frozen=True` blocks rebinding the field attribute, not in-place mutation — `topology.slot_to_teammate["x"] = "y"` would succeed silently. No live mutation path exists today: `server.instantiate_shape` builds the dict locally and passes it to `Topology`; no consumer mutates it post-construction.
- **Where**: `claude_crew/broker.py::Topology` dataclass
- **Why it matters**: The "frozen by construction" contract is partially misleading. A future consumer (e.g., M2 routing layer reading `BrokerSnapshot.topologies`) could inadvertently mutate the dict and corrupt shared snapshot state.
- **Suggested action**: Wrap `slot_to_teammate` in `types.MappingProxyType` at `Topology` init, or add a docstring contract: "caller must not mutate `slot_to_teammate` after construction." XS change.

### [Info] Partial-spawn window outside role-resolution scope

- **What**: `instantiate_shape`'s all-or-nothing guarantee (AT#14) applies specifically to the pre-flight role-resolution check. If `broker.spawn_teammate` raises mid-loop for an unrelated reason (broker teardown, resource exhaustion, OS-level failure), already-spawned nodes from earlier loop iterations persist while later ones are never spawned — a partial crew. This is outside the declared M0 scope (which scopes the guarantee to role resolution).
- **Where**: `claude_crew/server.py::instantiate_shape` spawn loop (post-pre-flight, post-approval)
- **Why it matters**: Not a current reliability concern; the spawn path is simple and stable. Notes for a future transactional-spawn pass if partial crews surface as a reliability issue in M2+ (when the spawn loop may be more complex).
- **Suggested action**: Defer. If partial crews surface, wrap the spawn loop in a try/except and tombstone already-spawned nodes on failure, returning `{ok:False, partial_crew:[...], error:...}`.

### [Info] Direct `proposal.status = "instantiated"` mutation

- **What**: `server.instantiate_shape` sets `proposal.status = "instantiated"` directly on the live `ShapeProposal` object rather than routing through a broker method. Minor encapsulation breach — the broker's state-machine boundary is partially bypassed. Functional and test-covered.
- **Where**: `claude_crew/server.py::instantiate_shape` (post-spawn success)
- **Why it matters**: Cosmetic now. If other callers want to know "has this shape been instantiated?" they must call `broker.get_proposal`, which is correct — but the mutation path bypasses any future hooks or guards the broker might add at status-transition time.
- **Suggested action**: Add `broker.mark_instantiated(shape_id)` (or extend `resolve_proposal` to accept a `"instantiate"` decision) in a future broker-encapsulation pass. Low priority; defer until state machine grows.

### [Info] Shape-gate panel renders active-instance proposals only

- **What**: `ShapeGatePanel` in `dashboard.html` reads `cli.shape_proposals` — the proposals of the currently selected instance only. A follower's pending proposal surfaces only when the operator explicitly switches to that follower instance in the dashboard. The multi-instance *approval* proxy (AT#12, `POST /shape-approval/{crew_id}/{shape_id}`) works correctly regardless of which instance is selected.
- **Where**: `claude_crew/ui/dashboard.html::ShapeGatePanel`
- **Why it matters**: An operator viewing the leader dashboard may miss a pending approval on a follower unless they notice the follower row and select it. Not a correctness defect (the approval mechanism is multi-instance aware), but a potential UX gap for operator awareness in a multi-crew setup.
- **Suggested action**: Product/UX decision: should the leader aggregate pending gates from all instances into one panel (with per-crew `crew_id` labels and Approve/Decline routing through the proxy path)? If yes, read from all aggregated `cli.instances` proposals, not just the selected one. Scope: S.

---

## Coordinator / Infra Observations (workflow-retro disabled — surfaced here)

Observations from this run of the repo-reactor coordinator and RR infra. Tagged by layer.

### [High, tooling] #45 SDK-crash-on-nonzero-Bash-exit — recurred twice in this run

- **What**: Two reviewer teammates (both `rr-slice-reviewer` instances) hard-crashed — SDK subprocess exit 1 — during this run. Triggers: (1) `uv run pytest` returned non-zero (2 pre-existing flaky `test_shutdown_signals` → exit 1 on the full suite); (2) `grep` with no match returned exit 1. Both caused the SDK teammate turn to abort with `{"error":"internal","message":"Command failed with exit code 1"}` rather than surfacing the non-zero result to the model. Recovery required: kill + re-spawn the reviewer, coordinator pre-runs test commands and supplies results as a ground-truth file, reviewer reads reports without running shell commands. This is the same bug documented 2026-06-04 (three incidents) and 2026-05-24 (three incidents). **Five+ recorded incidents across two feature runs.**
- **Where**: `claude_crew/sdk_teammate.py` turn loop (non-zero Bash exit → `ProcessError` → turn abort); `rr-slice-reviewer` / `rr-feature-reviewer` role definitions (grant Bash tool)
- **Why it matters**: Makes any Bash-running reviewer structurally fragile. Non-zero exits are normal tool output (failing tests, grep-no-match, git check on clean tree) — the model must see the result to react. Every crash costs a kill + re-spawn + coordinator workaround and degrades review quality when the reviewer is forced into read-only mode.
- **Suggested action**:
  - (a) **Land the #45 crash-guard**: feed the non-zero Bash exit as a tool result to the model (the subprocess's final output + exit code), not as a turn-fatal exception. The current `ProcessError("Command failed with exit code N")` surfaces when the CLI subprocess itself exits; the fix is session-resume or treating the exit as a structured tool result at the SDK boundary.
  - (b) **Independently**: make `rr-slice-reviewer` and `rr-feature-reviewer` **Read-only-by-contract** in the skill definition — coordinator supplies pre-run test output and git diff as ground-truth files; reviewer reasons from those, runs no Bash. This is the reliable path (verified this run); make it the standard, not the fallback. The read-only + ground-truth pattern removes the Bash dependency entirely for review roles.

### [Medium, tooling] context-mode tools wedge reviewer roles

- **What**: The first `rr-plan-reviewer` spawned in this run hung approximately 10 minutes inside a single `ctx_execute` call. `last_activity` flatlined; the tool never returned. Re-spawning a fresh plan-reviewer without context-mode MCP (CRG-only tool grant) fixed the problem immediately — the reviewer completed its review normally without context-mode tools.
- **Where**: context-mode MCP plugin (`mcp__plugin_context-mode_context-mode__*`) as granted to reviewer-role teammates in the coordinator's spawn configuration
- **Why it matters**: Reviewer roles (plan-reviewer, slice-reviewer, feature-reviewer) do not benefit from context-mode's sandbox-execution model. They read code and synthesize; they do not transform data pipelines. Granting context-mode adds a wedge/crash surface with no functional benefit. The wedge is silent — `last_activity` flatlines but no error is returned; the coordinator must detect staleness via the backstop timeout.
- **Suggested action**: Do not grant context-mode MCP to reviewer roles by default. The right code-intelligence tool for reviewers is the CRG graph (`crg` MCP — `semantic_search_nodes_tool`, `query_graph_tool`, `get_review_context_tool`). Context-mode is appropriate for implementor and planner roles that do data processing; reviewers only need structural navigation.

### [Medium, skill/coordinator] persistent reviewer anchors on stale same-slug verdict on re-review

- **What**: On a cycle-1 re-review (the spec had been revised since cycle-0 to address plan-review H1 and M1 blockers), the reused `rr-plan-reviewer` teammate re-emitted its cycle-0 verdict verbatim — twice — without re-reading the revised spec from disk. The model's prior context anchored its output despite the coordinator's request to re-review. Re-spawning a fresh reviewer instance fixed it immediately (fresh context, no prior verdict).
- **Where**: `rr-plan-reviewer` skill prompt; coordinator re-review discipline
- **Why it matters**: Persistent teammates accumulate turn context. When asked to "re-review," the model may rely on its cached understanding of the artifact rather than reading afresh. This produces reviews that appear to engage with the new artifact but are actually prior-cycle outputs — structurally unreliable as a quality gate.
- **Suggested action**:
  - (a) **Spawn fresh** on every re-review of a changed artifact. Do not reuse the prior-cycle reviewer instance. Fresh spawn = no prior-cycle context = correct anchor.
  - (b) If reuse is desired, the coordinator must include in the re-review prompt: "**The artifact CHANGED since your last review.** Discard your prior verdict entirely. Re-read the spec from disk (`<path>`) before reviewing. Do not rely on your previous assessment." Option (a) is the safer default.

### [Low, prompt] planner surveyed only Python server files for UI presence claims

- **What**: In cycle-0 planning, the planner grepped `ui_server.py` and concluded "no mermaid/diagram-viz layer exists in the current codebase." The mermaid renderer — `mermaid@11.4.1` loaded globally, `mermaid.initialize({securityLevel:'strict'})`, `renderMermaidBlocks`, DOMPurify pipeline — was in `claude_crew/ui/dashboard.html`. The planner's absence claim was incorrect because it only checked Python server modules, not the HTML/JS frontend assets. This caused a plan-review H-finding in cycle-0 that required a cycle-1 re-plan.
- **Where**: `rr-planner` skill system prompt (or coordinator planner guidance)
- **Why it matters**: In this codebase, the frontend is in `claude_crew/ui/dashboard.html` (React JSX, inline `<script>` blocks, CDN-loaded libraries). Python-only searches for UI capabilities produce structurally incorrect absence claims. UI claim misses like this create avoidable plan-review blockers.
- **Suggested action**: Add to the planner prompt or codebase survey step: "Before claiming a UI capability is absent, survey **both** the Python server modules (`ui_server.py`, `server.py`) **and** frontend asset files (`claude_crew/ui/*.html`, `*.js`) explicitly. Frontend logic — rendering pipelines, loaded libraries, React components — lives in `dashboard.html`, not in the Python modules."

---

## Roadmap Ordering Note

### [roadmap note] M2 precedes M1 in the Workflow Shape Composition execution order

- **Source**: Jerome, 2026-06-11 (confirmed during this feature's retro)
- **What**: The agreed next milestone after M0 is **M2** (edge routing enforcement + scoped teammate `send_to` + neighbor adjacency injection + circuit breaker), **not M1** (blessed shape library + lead router/classifier). Rationale: M2 makes the approved graph *execute* — it turns M0's declarative `Topology` into live routing behavior. M1 (shape templates in a `shapes/` dir, file-path `propose_shape`, problem→shape classification) is independent of M2 and higher-value after the graph runs.
  - Revised execution ordering: **M0 done → M2 next → M1 later (deferred) → M3/M4/M5 (deferred)**
  - The spec's §Out-of-scope lists M1 before M2 — this reflects creation order, not execution order. The roadmap in `doc/PRODUCT-VISION.md` should reflect the agreed M2-first ordering.
- **Suggested action**: In `doc/PRODUCT-VISION.md` feature pipeline, label `wsc-m2` as `next` and `wsc-m1` as `deferred (after M2)`. *(Applied in this doc-sync.)*
