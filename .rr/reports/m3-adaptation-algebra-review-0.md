# Plan Review: m3-adaptation-algebra (cycle 0)

**Spec:** /home/jerome/dev/claude-crew/.rr-worktrees/m3-adaptation-algebra/.rr/specs/m3-adaptation-algebra.md
**Cycle:** 0
**Reviewed:** 2026-06-17
**Verdict:** REQUEST-CHANGES

## Summary

A well-structured spec for a pure-data adaptation algebra (five verbs) plus one MCP tool reusing the
M1.5 gate verbatim. Provenance claims all check out against the tree (`register_proposal` carries
`adaptation_diff` at broker.py:1367; the `instantiate_shape` role-resolution seam exists at
server.py:919-936; `parse_shape` rejects zero-node shapes at shapes.py:87). AT coverage is otherwise
exhaustive and the two-task split is clean. The verdict is driven by one inverse-coverage gap:
`Swap`'s optional-field behavior (and its `render()` branch) is fully specified but exercised by no
acceptance test.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `spec.deliverable.untested` — Acceptance Tests / Data-API Contracts / Edge Cases: `Swap`'s
  optional-field handling is a named deliverable with **zero** AT coverage. The contract
  (`Swap # params: slot, role, model/extra_tools/extra_skills optional`), the Edge Case "swap optional
  fields — when `model`/`extra_tools`/`extra_skills` are supplied they replace the node's values and
  appear in the diff's `before`/`after`", and the `render()` template's `(+ "; {field} {old} -> {new}"
  per changed optional)` clause all describe behavior that **no acceptance test triggers**. AT 2, 8,
  25, and 28 every call `Swap(slot=…, role=…)` with no optional fields, so the field-replacement code
  path, its `before`/`after` diff population, and the `render()` per-optional clause could be absent or
  broken and the suite would stay green. Why it matters: an autonomous implementor can satisfy all 34
  ATs without ever implementing optional-field swap, shipping a silently incomplete `Swap`/`render()`.
  Fix: add-acceptance-test — add an AT under the happy-path block, e.g. "Given a 2-node shape whose
  `reviewer` node has `model='sonnet'`, when `Swap(slot='reviewer', role='security-reviewer',
  model='opus', extra_tools=(…)).apply(shape)` runs, then the node's `model`/`extra_tools` are
  replaced, the diff's `before`/`after` include those fields, and `.render()` emits the
  `; model sonnet -> opus` clause." Then re-point AT 7's golden to include that diff.

### Medium

- [MEDIUM-01] `spec.acceptance-tests.incomplete-coverage` — AT 6 (round-trip invariant): the
  `parse_shape(shape_to_dict(result)) == result` invariant is asserted only against the AT 1–5 result
  shapes. `Shape` carries `phases`, and `ShapeNode` carries `cwd`/`model`/`extra_tools`/`extra_skills`,
  and `ShapeEdge` carries `reverse_mode` (per ARCHITECTURE.md shapes.py table). If the AT 1–5 base
  shapes are minimal (no `phases`/`cwd`/`model`/`reverse_mode`), `shape_to_dict` could silently omit
  one of those fields and AT 6 still passes — a latent round-trip bug for any shape that uses them.
  Why it matters: `shape_to_dict` is the inverse-of-`parse_shape` deliverable; partial-field coverage
  lets it ship incomplete. Fix: add-acceptance-test — have at least one AT 6 input shape carry
  `phases`, a node `cwd`/`model`, and an edge `reverse_mode`, so the round-trip pins every field.

### Low

- [LOW-01] `spec.data-api.ambiguous` — `adapt_shape` success envelope: the return shape lists
  `{ok, shape_id, status, diff, shape}` but does not state whether `shape` is a `Shape` instance or its
  `shape_to_dict` form. MCP returns must be JSON-serializable, so it must be the dict form; the spec
  should say so explicitly. No AT asserts on the returned `shape`, so this is advisory. Fix:
  clarify-assumption.
- [LOW-02] `spec.acceptance-tests.gap` — `AdaptationChain.adapt` error propagation when a chained
  step's `apply` raises `ShapeValidationError` is unspecified and untested (AT 8 covers only the happy
  two-step chain). Natural exception propagation is the obvious behavior, so this is low risk. Fix:
  add-edge-case (optional).

## Persistent Findings

_None — first cycle or no recurrence._

## Spec Quality

- **Acceptance tests:** Clear (one coverage gap — HIGH-01)
- **Test command:** Present and runnable (`uv run pytest`, full suite per the widely-consumed-substrate mandate; per-task `testCommand`s present)
- **Scope boundary:** Clear (M3.5 live-reshape, M4/M5, broker persistence, dashboard work, helper extraction all explicitly out of scope)

## Verdict Rule Applied

REQUEST-CHANGES: 1 High finding (inverse-coverage trigger — a named deliverable, `Swap` optional-field
handling and its `render()` branch, has no acceptance-test coverage). All provenance claims verified;
AT-to-task coverage is otherwise complete (1–34 each claimed exactly once across two cleanly-split
tasks with one correct dependency edge).
