# Slice Review: workflow-shape-composition-m0 task=hardening

**Cycle:** 0 · **Verdict:** PASS

Focused 3-check review of the 5-fix hardening batch on M0 shape-composition code.
Source inspected via Read/Grep (read-only); all gates were coordinator-run
(shape suite 117 passed exit 0; full suite 1440 passed exit 0, no regressions).

---

## Check 1 — Slice adherence (all 5 fixes correct + non-vacuously tested)

### Fix 1 — `resolve_proposal` source-state guard ✅
- Guard added **after** invalid-decision check and **after** unknown-id KeyError
  (`broker.py` diff lines): `if proposal.status != "pending": raise ValueError(...)`.
  Guard order (decision → unknown-id → wrong-status) is sensible.
- Tests (`test_shape_broker.py`): double-approve raises, double-decline raises,
  timed_out→resolve raises, and error text asserts the current state string
  (`match="approved"`). Sad paths real; happy path still covered by AT5 tests.

### Fix 2 — shared `resolve_role` (the subtle one) ✅
- `factories.py` exposes `factory.resolve_role = _resolve_role` (same idiom as
  `known_roles`). `server.py` pre-flight prefers it: `resolved = resolve_role_fn(role);
  if resolved not in known: unresolved.append(role)`, with the inline exact/suffix
  fallback retained **only** when `resolve_role` is absent (stub).
- **Semantic equivalence verified against the real `_resolve_role`**
  (`factories.py:380-414`), not just the test mirror:
  - exact match → returns `requested` (∈ known) → accepted ✓
  - unique `:role` suffix → returns promoted candidate (∈ known) → accepted ✓
  - multiple candidates → returns original `requested` (∉ known) → unresolved ✓
  - zero candidates → returns original `requested` (∉ known) → unresolved ✓
  This is exactly the old inline rule (exact / unique-promote / ambiguous→unresolved
  / zero→unresolved). The `_make_resolve_role` test helper faithfully mirrors it.
- Tests: unique-suffix promotion via injected `resolve_role` → `ok:True`, 2 crew;
  ambiguous → `ok:False`, `unresolved_roles` contains `builder`, zero spawn. AT14
  (which injects `known_roles` only) still exercises the fallback path and passes.

### Fix 3 — `Topology.slot_to_teammate` immutable ✅
- Frozen dataclass `__post_init__` does `object.__setattr__(..., MappingProxyType(dict(...)))`;
  annotation now `Mapping[str, str]`. The `dict()` copy means the proxy owns its data,
  so caller-side mutation post-construction can't leak in.
- Tests: in-place write raises `TypeError`; reads + `.keys()` work; snapshot round-trip
  preserves content (proxy `== dict`) and still refuses mutation; caller-dict mutation
  after construction doesn't affect the proxy.

### Fix 4 — transactional spawn ✅
- Spawn loop wrapped in `try/except`; on any spawn raise, every already-spawned
  teammate is killed via the **real** `broker.kill_teammate(..., reason="spawn-rollback",
  graceful=False)`, returns `{ok:False, error, partial_crew_rolled_back}`. Proposal is
  **not** marked instantiated (left `approved`); topology **not** recorded (return precedes
  both). Inner kill exceptions swallowed best-effort so they don't mask the original error.
- Test: `spawn_teammate` patched to raise on call #2 of a 3-node shape → `ok:False`,
  `list_crew` shows **zero alive**, proposal still `approved`. (Note: had `kill_teammate`
  rejected the kwargs, the inner `except` would swallow it and the teammate would survive —
  the `alive == []` assertion would then fail. It passes, confirming the kill kwargs are valid.)

### Fix 5 — `broker.mark_instantiated` ✅
- New method: KeyError on unknown id, ValueError unless status == `approved`, else sets
  `instantiated`. `server.py` replaced the direct `proposal.status =` write with
  `broker.mark_instantiated(shape_id)`.
- Tests: pending/declined/already-instantiated → ValueError, unknown → KeyError,
  approved → instantiated (happy). AT8 confirms full end-to-end still instantiates.

---

## Check 2 — Non-regression

- Established green per coordinator ground truth (full suite 1440 passed, no regressions).
- **Fix 2 resolver sharing:** the shared closure read live off `holder.pack` is the same
  function the spawn path already uses; pre-flight is read-only. No behavior change for
  callers that lack `resolve_role` (fallback identical to prior inline rule).
- **Fix 3 annotation/return-type → Mapping:** grep of `claude_crew/` for `slot_to_teammate`,
  `get_topologies`, `.topologies` shows the **only** serialization path is `server.py:928`,
  which serializes a separate local plain `dict`, not the proxy. No consumer mutates the
  proxy or `json.dumps` it directly; topologies are not yet surfaced to the dashboard JSON.
  `MappingProxyType` compares equal to dict and is iterable, so existing read/equality
  assertions are unaffected. Safe.

---

## Check 3 — Code-quality smoke

- Diffs are tight, well-commented, and each fix references its rationale. Error messages
  are informative (include id + offending state). Guard ordering is deliberate.
- No new smells. `# type: ignore` annotations on the attribute-injection idiom match the
  existing established pattern (`known_roles`, `startup_diagnostics`).

---

## Judgment calls (Info — non-blocking)

- **Fix 2 double-logging:** `_resolve_role` logs INFO/WARN both at pre-flight and at spawn,
  producing two lines per promotion. Flagged in the build report, acceptable per task, and
  arguably useful (confirms pre-flight ran). No quiet-path over-engineering. Accepted.
- **Fix 4 `approved` vs `failed` state:** implementor chose to leave a failed instantiation
  at `approved` (retryable) rather than introduce a `failed` terminal status. Reasonable —
  a new status would ripple into `resolve_proposal`/`mark_instantiated`/UI for no clear
  operator benefit. Accepted.

No Critical or High findings.
