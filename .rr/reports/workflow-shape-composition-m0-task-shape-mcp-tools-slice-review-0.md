# Slice Review: workflow-shape-composition-m0 task=shape-mcp-tools

## Scope
Task index 2, owns acceptance tests 8, 9, 10, 14. Reviewed from coordinator ground-truth diff (`factories.py` +4, `server.py` +168) and `tests/test_shape_gate.py` (11 tests). Coordinator-run: slice green (11 passed, exit 0), full suite 1380 passed + 2 acknowledged pre-existing `test_shutdown_signals` baseline flakes. Read-tool-only review.

## Check 1 — Slice adherence (ATs 8, 9, 10, 14)

**`factory.known_roles` accessor — PASS.** Added only to `default_factory` (SDK path) as `factory.known_roles = lambda: tuple(holder.pack.keys())`, reading `holder.pack` live so post-`refresh` state stays current — faithfully mirroring the adjacent `factory.startup_diagnostics`/`factory._holder` accessor idiom. The stub factory leaves it unset, exactly as required for the skip-preflight path.

**`propose_shape` — PASS (AT8).** Parses via `parse_shape`; `ShapeValidationError` → `{ok:False, stage:"parse", error:...}` (asserted by `test_propose_shape_parse_error_returns_ok_false`). On success registers the proposal with optional `adaptation_diff` and blocks on `await_proposal(timeout=timeout_seconds)` (default 600), returning `ok/shape_id/status/shape` summary. Status values `approved|declined|timed_out` all exercised.

**`instantiate_shape` — PASS (AT9, AT10, AT14).**
- Unknown shape_id → `ok:False` (`test_unknown_shape_id_refused`).
- `status != "approved"` → `ok:False`, covering pending (AT9 `test_pending_proposal_refused`), declined (AT10 `test_decline_aborts_spawn`), timed_out (`test_timed_out_proposal_refused`), and instantiated (single-use, `test_instantiate_marks_proposal_instantiated`). Each asserts `list_crew == []` → zero spawns.
- Pre-flight (AT14): when `factory.known_roles` is present, resolves every node role by exact match or **unique** `*:role` suffix promotion (mirrors `factories._resolve_role`); any unresolvable role returns `{ok:False, unresolved_roles:[...]}` and spawns nothing. Otherwise spawns one teammate per node (`name=slot`), records an immutable `Topology`, and marks the proposal `"instantiated"`.

**CRITICAL all-or-nothing check — SATISFIED.** `test_unresolvable_role_refuses_all_zero_spawn` injects `known_roles=("builder","sentinel")` and approves `_SHAPE_UNRESOLVABLE`, whose **first node (`builder`) is resolvable** and second (`nonexistent-role`) is not. The result asserts `ok:False`, `nonexistent-role` in `unresolved_roles`, **and `list_crew == []`** — proving the resolvable node was *not* partially spawned. This is a genuine all-or-nothing assertion, not a degenerate single-bad-node case. AT#9's no-spawn-before-approval and AT#10's no-spawn-on-decline are likewise asserted via empty `list_crew`. Pre-flight is well-covered: unique-suffix promotion accepted, ambiguous suffix (`alpha:builder`/`beta:builder`) refused, and no-`known_roles` skip — all non-vacuous (assert real crew counts, topology edges, status transitions).

## Check 2 — Non-regression
Purely additive: 172 insertions across two files, three new symbols (`propose_shape`, `instantiate_shape` tools, `factory.known_roles` attribute) plus two imports (`Topology`, `parse_shape`/`ShapeValidationError`). No existing code path is modified — the `known_roles` accessor is an additive attribute the new tool reads via `getattr(..., None)`, so absence is the backward-compatible default. Full suite green minus the two documented baseline flakes. No plausible cross-module breakage.

## Check 3 — Code-quality smoke
Clean and well-documented (thorough docstrings on both tools spelling out the contract). The pre-flight algorithm correctly handles exact match, unique-suffix promotion, and ambiguity (two suffix candidates → unresolved). `getattr(factory, "known_roles", None)` guard is the right shape for the stub/SDK asymmetry. No blocking smells.

## Observations (Info tier — do not affect verdict)
- **Pre-flight vs. spawn-time resolution duplication.** The suffix-promotion logic is re-implemented here rather than shared with `factories._resolve_role`. If that resolver ever changes, the two can drift (a role passing pre-flight but failing at spawn, or vice versa). A shared helper would be more robust; acceptable for M0.
- **Partial-spawn window outside role resolution.** Pre-flight guarantees no partial spawn *due to unresolvable roles*, but if `broker.spawn_teammate` raised mid-loop for an unrelated reason, already-spawned nodes would persist. Outside AT#14's stated scope (role resolution), so not a defect here — noting for the feature reviewer.
- **Direct `proposal.status = "instantiated"` mutation** rather than a broker method is a minor encapsulation smell; functional and verified by test.
- Cross-slice: `Topology`, `record_topology`, `register_proposal`, `await_proposal`, `parse_shape`, `ShapeValidationError` are owned by sibling slices — integration coherence is the feature-reviewer's charter.

## Verdict
All three checks pass. No Critical or High findings. The slice implements ATs 8/9/10/14 faithfully, with a genuine all-or-nothing zero-spawn test and an additive, non-regressing footprint.

**Verdict:** PASS
