# Slice Review: m3-adaptation-algebra task=adapt-shape-tool

**Verdict: PASS** — no Critical/High findings across the three checks.

## Check 1 — Slice adherence (ATs 23–33)

The `adapt_shape` tool (server.py:1044) implements the spec's five-stage flow with the
failure-envelope contract intact. Reviewed by reading the implementation against each AT.

**No-proposal-on-failure invariant — HOLDS structurally.** `broker.register_proposal` is
called exactly once, at step 5 (server.py:1202), *after* every guard. Every failure path
`return`s before reaching it:
- `stage:"base"` — both/neither base args (1073), unknown id (1082), status ∉ {pending,approved} (1088). ✓ AT29/30/31
- `stage:"parse"` — inline `parse_shape` raises (1102). ✓
- `stage:"verb"` — verb ∉ five (1109). ✓ AT32
- `stage:"adapt"` — unresolvable role (1149) and `ShapeValidationError`/`KeyError`/`TypeError` from `apply` (1195–1198). ✓ AT26/27/33

Tests assert the *proposal count*, not merely `ok:False`, on each sad path:
AT26 (line 274), AT27 (313), AT29 (371), AT31a (482), AT32 (543), AT33 (574) assert
`len(snap.shape_proposals) == 0`; AT30 instantiated (405) asserts no *new* proposal;
AT31b (512) asserts the pre-existing proposal count is unchanged. ✓

- **Pre-instantiation guard (AT30):** status check at 1088 rejects `instantiated`/`declined`/
  `timed_out`; the tool only reads `broker.get_proposal` — it never touches the teammate
  registry. Tests cover all three terminal statuses (373/408/435). ✓
- **Role-resolution reuse (AT25–28):** the `getattr(factory,"known_roles",None)` +
  `resolve_role`/promotion-fallback idiom at 1132–1148 mirrors `instantiate_shape`'s
  pre-flight (932–955); resolves only the swap/augment role; skipped when `known_roles`
  absent (1133). For `augment` it reads the augmenting node's role (1125–1130). ✓
- **Iterative re-gate (AT24):** approved `base_shape_id` accepted (status ∈ {pending,approved});
  second adapt registers a new distinct pending `shape_id`. Test asserts distinctness +
  two proposals (198–205). ✓
- **Dict-form return:** success `shape` = `shape_to_dict(new_shape)` (1209), JSON-serializable.
  AT23 asserts equality with `shape_to_dict` (129). ✓
- **Wired as MCP tool:** `@mcp.tool()` decorator at server.py:1044; all shapes-algebra symbols
  imported (server.py:30–43, confirmed via Grep). ✓

## Check 2 — Non-regression

Per coordinator-authoritative results: slice command 38 passed, task-0
(`test_shape_adaptation.py`) 45 passed. Scope is `server.py` (+182) and the new test file
only; `broker.py`/`shapes.py` untouched — matches `taskTouches` and the load-bearing
"broker.py not modified" decision. `register_proposal(adaptation_diff=...)` reuses the
existing M1.5 gate signature; no second approval path introduced. No regression surface.

## Check 3 — Code-quality smoke

Clean. The implementation faithfully mirrors the established `instantiate_shape` seam,
list→tuple coercion for `extra_tools`/`extra_skills` is handled for both node dicts
(`_node_from_dict`) and swap params, and malformed `params` surface as `stage:"adapt"` via
the `KeyError`/`TypeError` catch (1197) rather than an unhandled 500. Docstring matches the
contract. Minor (non-blocking) nits, not findings: `_KNOWN_VERBS` is rebuilt per call, and
the role-resolution block duplicates the instantiate idiom — both are explicitly deferred as
cleanup by the spec (Out of Scope: "Extracting a shared role-resolution helper"). Nothing
actionable.

## Severity summary
- Critical: 0
- High: 0
- Medium: 0
- Low: 2 (per-call `_KNOWN_VERBS`; intentional resolution duplication — both spec-sanctioned)

RR-VERDICT: PASS m3-adaptation-algebra 0 .rr/reports/m3-adaptation-algebra-task-adapt-shape-tool-slice-review-cycle-0.md
