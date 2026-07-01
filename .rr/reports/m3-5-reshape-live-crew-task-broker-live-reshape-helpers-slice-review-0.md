# Slice Review: m3-5-reshape-live-crew task=broker-live-reshape-helpers

**Task:** `broker-live-reshape-helpers` (index 1) — owns AT 19 (D2 helper unit).
**Cycle:** 0. **Verdict:** PASS.

## Summary

Three additive broker helpers land cleanly on `claude_crew/broker.py:1621-1652`, backed by a new 19-case unit suite (`tests/test_reshape_broker_helpers.py`). Diff is `+33 -1` — the single deletion is the widened import line (`from collections.abc import Iterable, Mapping`), itself additive. No existing method was altered; `promote_edge` (line 1620), `record_topology` (1474), `get_topologies` (1478), and `_effective_edge_mode`'s override lookup (1512) are untouched. Both required test commands exit 0 in my re-run.

## Slice adherence (AT 19)

AT 19 decomposes into three contract clauses; all satisfied by implementation and covered in the pass list:

- **`latest_topology()` → `None` on empty, most-recent `Topology` after `record_topology`.** Impl: `return self._topologies[-1] if self._topologies else None` (1631). Non-mutating. Covered by `TestLatestTopology` (4 cases incl. `test_does_not_remove_topology_from_list`). ✓
- **`set_edge_override("a","b","tee")` writes `_edge_overrides[("a","b")] == "tee"`.** Impl: `self._edge_overrides[(from_slot, to_slot)] = mode` (1642) — generalizes `promote_edge`'s hardcoded `"gated"`. Covered by `TestSetEdgeOverride` (8 cases incl. `test_consistent_with_promote_edge`). ✓
- **`remove_edge_overrides([("a","b"),("x","y")])` deletes present, silently skips absent (idempotent).** Impl: `self._edge_overrides.pop(pair, None)` per pair (1652). Covered by `TestRemoveEdgeOverrides` (8 cases: mixed present/absent, double-remove, empty iterable, generator input, unrelated-override preservation). ✓

The adversarial focus items check out: `remove_edge_overrides` is genuinely idempotent on absent keys (`.pop(k, None)`, never raises `KeyError`), and the change is additive-only.

## Scope (Invariant 1)

Declared `taskTouches`: `claude_crew/broker.py`, `tests/test_reshape_broker_helpers.py`. `git diff --name-only HEAD` + untracked = exactly those two. No violation.

## Non-regression

- `uv run pytest tests/test_reshape_broker_helpers.py` → **19 passed** (exit 0).
- `uv run pytest tests/test_broker.py` → **105 passed** (exit 0).

## Findings

### Critical / High / Medium / Low
_None identified._

### Info
- [INFO-01] `slice.review-process.cross-slice-observation` — `set_edge_override`/`remove_edge_overrides` are the D2 surface `reshape_crew` (task index ≥2) will consume; the helpers accept arbitrary mode strings and unknown slot pairs with no validation (by design, per docstrings). Whether the caller (`server.py reshape_crew`) validates `mode ∈ {gated,tee,direct}` before delegating is the feature-reviewer's integration concern, not this slice's.

## Code-quality smoke

`claude_crew/broker.py`: docstrings accurate; no secrets, no swallowed exceptions, no dead code, additive-only. `tests/test_reshape_broker_helpers.py`: imports at module top, clear class-per-helper structure, happy + sad paths. Minor cosmetic nit (below Low): two `from claude_crew.broker import ...` lines could merge.

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| INFO-01 | Info | slice.review-process.cross-slice-observation | deferred-with-rationale | Mode-validation is a `reshape_crew` integration concern for the feature-reviewer, outside this D2-helper slice. |

RR-VERDICT: PASS m3-5-reshape-live-crew 0 (verdict line recorded by coordinator)
