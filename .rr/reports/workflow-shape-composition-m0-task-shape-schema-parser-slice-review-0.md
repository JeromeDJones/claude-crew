# Slice Review: workflow-shape-composition-m0 task=shape-schema-parser

**Cycle:** 0 · **Scope:** `claude_crew/shapes.py`, `tests/test_shapes.py` (untracked, uncommitted) · ATs 1–4.

## Check 1 — Slice adherence (ATs 1–4)

Verified each acceptance test against the spec watchpoints, and confirmed the tests genuinely assert (not vacuous):

| Spec requirement | Impl | Test assert | Status |
|---|---|---|---|
| AT1 happy: nodes/edges match, explicit `gated` recorded, omitted mode → `gated` | `parse_shape` builds frozen `Shape`; `mode_raw = raw_edge.get("mode","gated")` | `test_explicit_mode_gated`, `test_omitted_mode_defaults_to_gated`, node slot/role asserts | ✓ |
| AT1 mermaid: `graph TD` + slot labels + mode-labeled edge | `shape_to_mermaid` emits `graph TD`, `slot["slot\nrole"]`, `-->|mode|` | `test_mermaid_starts_with_graph_td`, `_contains_slot_labels`, `_contains_role_labels`, `_contains_mode_label` | ✓ |
| AT2 dangling edge raises, **names** offending slot | `from_slot/to_slot not in seen_slots` → raises with slot in message | `test_dangling_to_slot_raises` + `_from_slot` + `_error_message_names_offending_slot` (uses unique sentinel) + `_no_shape_produced` | ✓ |
| AT3 duplicate slot raises | `if slot in seen_slots: raise` | `test_duplicate_slot_raises`, `_error_is_validation_error`, plus `_distinct_slots_ok` control | ✓ |
| AT4a zero nodes raises | `len(raw_nodes)==0` → raise; also missing key | `test_zero_nodes_raises`, `_missing_nodes_key_raises` | ✓ |
| AT4b bad mode raises | `mode_raw not in _VALID_MODES` | `test_invalid_mode_raises` + `_not_in_allowed_set` (incl. `GATED`/`Gated` case-sensitivity) | ✓ |
| AT4c missing role raises | empty/missing `role` → raise | `test_node_missing_role_raises`, `_empty_role`, `_missing_slot` | ✓ |
| AT4d unknown shape-level key raises | `set(keys) - _SHAPE_KEYS` | `test_unknown_shape_key_raises` | ✓ |
| AT4e self-loop raises | `from_slot == to_slot` → raise | `test_self_loop_raises` | ✓ |
| `phases` verbatim + EXEMPT from unknown-key guard | `phases` is a known shape key; entries `tuple(raw_phases)` with no per-entry validation | `test_phases_with_arbitrary_keys_does_not_raise`, `_recorded_verbatim`, `_multiple_entries_verbatim` | ✓ |

Beyond-required guards from the spec's Edge Cases also present and tested: unknown **node**-level key (`test_unknown_node_key_raises`), unknown **edge**-level key (`test_unknown_edge_key_raises`), duplicate identical edge (`test_duplicate_edge_raises`), empty/whitespace name + empty description. All raise loudly via `ShapeValidationError` (subclass of `ValueError`) — no silent drops, no partial `Shape` (`test_no_shape_produced_on_dangling_edge` proves it). Default `source` is threaded into every message for diagnostic locality.

## Check 2 — Non-regression

Re-ran the slice gate myself:

```
uv run pytest tests/test_shapes.py  →  45 passed in 0.05s  (exit 0)
```

First task in the DAG; no sibling test commands to honor. Matches build report (45/45).

## Check 3 — Code-quality smoke (changed files only)

Clean, single-responsibility module; pure data, no broker/SDK coupling as the spec demands. Frozen dataclasses match the contract field-for-field. Mode + reverse_mode share one `_VALID_MODES` set. Tests are well-partitioned by AT with descriptive names and non-vacuous assertions (sentinel-based message checks, frozen-mutation checks, positive controls alongside sad paths).

No Critical/High/Medium findings. Minor observations (Info, non-blocking):

- **[Info] `phases` entries not type-checked.** `phases: tuple(raw_phases)` records verbatim per spec, but if `raw_phases` is a non-list iterable (e.g. a YAML scalar string) it would coerce oddly (`tuple("abc")→('a','b','c')`) or `TypeError` rather than `ShapeValidationError` for a non-iterable. Spec explicitly scopes `phases` as "recorded verbatim, not key-validated," so loud structural validation isn't required here — flagging only as future-hardening if `phases` ever gains semantics.
- **[Info] Mermaid node-id uses raw `slot`** as the mermaid identifier (`{slot}["..."]`). Slots with spaces/special chars could produce invalid mermaid. Out of this slice's ATs — XSS/escaping of label text is owned by the dashboard render slice (AT#13, `securityLevel:'strict'` + DOMPurify). No action here.
- **[Info / cross-slice]** `from_slot`/`to_slot` use bare `not from_slot` truthiness vs the `.strip()` used for `slot`/`name`. Whitespace-only edge endpoints would pass the emptiness check but then fail the dangling-slot guard (raises anyway), so behavior is still loud. Cosmetic consistency only.

## Verdict

Slice fully satisfies ATs 1–4 with genuine, non-vacuous assertions; every spec watchpoint (loud validation, shape/node/edge unknown-key rejection, `phases` verbatim+exempt, omitted-mode→`gated`, no self-loops, no dup edges, `graph TD` mermaid) is implemented and tested. Gate green in my own re-run. No Critical or High findings.

**Verdict:** PASS
