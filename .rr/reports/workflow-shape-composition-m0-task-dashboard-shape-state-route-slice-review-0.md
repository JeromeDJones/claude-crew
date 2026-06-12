# Slice Review: workflow-shape-composition-m0 task=dashboard-shape-state-route

## Scope
Task index 3, owns acceptance tests 11 (single-instance state + approval) and 12 (multi-instance proxy). Reviewed from coordinator ground-truth diff (`claude_crew/ui_server.py`, +117) and the new `tests/test_shape_dashboard.py` (24 tests). Coordinator-run: slice green (24 passed, exit 0), full suite 1408 passed. Read-tool-only review.

## Check 1 — Slice adherence (ATs 11, 12)

**(a) Pending proposals in state — PASS.** `_build_local_instance` adds a `shape_proposals` list iterating `snapshot.shape_proposals`, each carrying `shape_id`, `crew_id` (from `snapshot.crew_id`), `status`, `adaptation_diff`, and a pre-rendered `mermaid` via `shape_to_mermaid(p.shape)`. All five required fields present. Tests assert real behavior: key present, empty-when-none, pending surfaced with matching `crew_id`, mermaid non-empty + contains `graph TD`/slot labels/edge mode, mermaid byte-equal to `shape_to_mermaid(shape)`, adaptation_diff both None and set, multiple proposals, and approved-still-appears. Non-vacuous.

**(b) POST /shape-approval/{crew_id}/{shape_id} — PASS.** Handler validates both path params against `_PATH_PARAM_RE` → 400 `invalid_param`; parses body with try/except → 400 `bad_request` on malformed JSON; restricts `decision` to `approve`/`decline` → 400 `invalid_decision` otherwise (correctly rejects `edited` and missing key, per M0 approve/decline-only contract). Unknown shape_id → 404, internal error → 500. Route registered with `methods=["POST"]`. Tests cover every branch including the await_proposal unblock path.

**(c) Local-vs-proxy routing — PASS.** `crew_id == self._own_crew_id()` resolves locally via `broker.get_proposal`/`resolve_proposal`; otherwise delegates to `_proxy_shape_approval`, which mirrors `_proxy_artifact`: registry lookup by `crew_id`, robust port validation (`isinstance(port, bool)`-guarded int range), POST to `http://127.0.0.1:{port}/shape-approval/...`, 404 when registry None/crew absent/port bad, 502 on connection or JSON failure. The follower receives its own `crew_id` and serves locally — no re-proxy loop.

**CRITICAL multi-instance check — SATISFIED.** `test_leader_proxies_approval_to_follower` is a **genuine** AT#12 proxy test: it starts a second `UIServer` (`broker_b`) on a real free TCP port via `ui_b.serve()`, registers it in `InstanceRegistry`, POSTs to the leader (`broker_a`) for the follower's `crew_id`, and asserts both the HTTP 200 `approved` response **and** `broker_b.get_proposal(shape_id).status == "approved"` — proving the byte actually crossed the leader→follower boundary, not a single-instance shortcut. Backed by decline variant, unknown-crew→404, no-registry→404, and local-served-directly. This directly honors the CLAUDE.md multi-instance trap rule.

## Check 2 — Non-regression
Diff is purely additive: one import (`shape_to_mermaid`), one dict key in `_build_local_instance`, two new methods, one new route. No existing code paths modified. Full suite 1408 passed per ground-truth. No plausible cross-module breakage.

## Check 3 — Code-quality smoke
Clean. Error taxonomy is explicit and documented in the docstring; port guard correctly rejects bool-as-int; proxy mirrors the established `_proxy_artifact` pattern; broad `except` at the HTTP boundary is appropriate and logged via `_logger.exception`/`warning` with `exc_info`. No smells warranting change.

## Observations (Info tier — do not affect verdict)
- The state field derives own crew via `snapshot.crew_id` while the handler routes via `self._own_crew_id()`. Consistent for a single local broker; worth a one-line confirmation that both resolve identically, but not a defect.
- Cross-slice: `_PATH_PARAM_RE`, `_own_crew_id`, `Broker.register_proposal/get_proposal/resolve_proposal/await_proposal`, and `shape_to_mermaid` are consumed here but owned by sibling slices — integration coherence is the feature-reviewer's charter, noted only for routing.

## Verdict
All three checks pass. No Critical or High findings. Slice faithfully implements ATs 11 & 12 with a real multi-instance proxy test, additive footprint, and clean code.

**Verdict:** PASS
