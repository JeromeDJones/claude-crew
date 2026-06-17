# Feature Review: m3-adaptation-algebra

**Verdict:** PASS  •  **Cycle:** 0

## Scope & method
Synthesis gate over the two slice-reviewed-and-PASSed slices: `shape-adaptation-algebra` (task 0, `shapes.py` — the five verbs, `AdaptationDiff`, `AdaptationChain`, `shape_to_dict`) and `adapt-shape-tool` (task 1, `server.py::adapt_shape`). I did not re-audit per-slice adherence (slice-review owns that); I evaluated the **seam** between the two slices, holistic spec satisfaction across all 35 ATs, and cracks neither slice could see. I read both source bodies directly, the `instantiate_shape` seam they claim to mirror, all six on-disk reports, the spec, and `doc/ARCHITECTURE.md`. Non-regression: full `uv run pytest`.

## Check 1 — Cross-slice integration coherence — PASS
The tool→algebra contract is wired correctly at every point:

- **Verb construction matches constructor signatures.** `adapt_shape` (server.py:1170-1192) builds each command from `params`: `AddNode(node, edges)`, `Swap(**swap_params)`, `Augment(node, edges)`, `SetGate(**params)`, `Drop(**params)` — each matches the frozen-dataclass field set in shapes.py (340-680). Unknown/extra keys surface as `TypeError`→`stage:"adapt"` (server.py:1197), so malformed params can never reach `register_proposal`.
- **`_UNSET` sentinel preserved across the JSON boundary.** `Swap`/`SetGate` distinguish "omitted" (`_UNSET`, retain existing) from "supplied" (replace). The tool passes only the keys present in `params` via `Swap(**swap_params)`, so an absent optional → `_UNSET` → retained; a present value (even JSON `null`) → replaced. This is the correct semantics and is exactly what AT 35 (`extra_skills None -> ("audit",)`) and the round-trip ATs depend on.
- **List→tuple coercion is consistent on both sides.** `_node_from_dict` and the swap-params block coerce `extra_tools`/`extra_skills` lists→tuples (server.py:1161-1168, 1179-1182); `shape_to_dict` serializes tuples→lists (shapes.py:744-746); `parse_shape` coerces back. The round-trip the two slices each half-own closes cleanly.
- **Role-resolution idiom genuinely mirrors `instantiate_shape`.** server.py:1132-1148 reproduces the `getattr(factory,"known_roles")` + optional `resolve_role` + `endswith(":role")` uniqueness fallback from instantiate_shape (932-955). Difference is correct-by-design: `adapt_shape` checks only the single introduced role (swap's `params["role"]`, augment's `params["node"]["role"]`) rather than iterating all nodes — the new shape's other nodes were already resolved when they entered. Skip-when-absent behavior is identical (AT 28).
- **Gate reuse is verbatim.** `broker.register_proposal(new_shape, adaptation_diff=diff.render())` (server.py:1202) — no new gate path, no broker change. `doc/ARCHITECTURE.md:56` confirms `register_proposal(shape, adaptation_diff?)` was built with this channel. The success envelope returns `shape_to_dict(new_shape)` (JSON-serializable), not a raw `Shape` — correct for an MCP return.

## Check 2 — Holistic spec satisfaction — PASS
- **All 35 ATs covered end-to-end.** ATs 1–22, 34, 35 land in `test_shape_adaptation.py` (45 passed); ATs 23–33 in `test_shape_adapt_tool.py`/`test_shape_gate.py` (38 passed). The HIGH (AT 35 optional-field swap) and MEDIUM (AT 6 rich-field round-trip) raised at plan-review cycle 0 were resolved before build and are exercised.
- **M1.5 gate reused, not reforked.** `broker.py` is untouched (confirmed via `git diff --name-only`: only `server.py`, `shapes.py`, two new test files). No second approval path. Iterative re-gate (AT 24) works because each `adapt_shape` returns a fresh pending `shape_id`.
- **Pre-instantiation-only guard holds.** server.py:1088 rejects any base whose status ∉ {pending, approved} at `stage:"base"` (AT 30); the tool only *reads* `proposal.shape` and never touches the live teammate registry. `Drop`'s live-edge + ≥1-node guards (shapes.py:651-664) prevent producing an un-instantiable shape.
- **No-proposal-on-failure invariant holds.** `register_proposal` is called exactly once, at the very end (server.py:1202), after every guard returns early. All five failure stages (base/parse/verb/adapt incl. unresolved_roles) return before it.

## Check 3 — Cracks-fell-through — none material
- **`shape_to_dict` round-trips everything the tool returns.** It preserves `phases`, `cwd`, `model`, `extra_tools`, `extra_skills`, and edge `reverse_mode` (shapes.py:727-771), omitting only None/empty where `parse_shape` reconstructs identically. `SetGate` retains an existing `reverse_mode` when omitted (shapes.py:595-597), so a gate change can't silently drop reverse routing — the field survives the gate→serialize→re-parse path task 1 returns.
- **Diff string is the gate-visible artifact and the stored one.** `diff_str = diff.render()` is used for both the return value and `adaptation_diff=` (server.py:1201-1208) — operator sees exactly what's persisted. No divergence.
- The Info-tier cross-slice observations from both slice-reviews (intentional role-resolution duplication; near-identical edge-validation loops in `AddNode`/`Augment`; per-call `_KNOWN_VERBS` rebuild) are spec-sanctioned deferrals, not seam defects. They belong in the backlog, not this gate.

## Non-regression (feature-level)
`uv run pytest` → **1611 passed, 34 skipped, 1 xfailed, 2 failed** in 250s. The 2 failures are `tests/test_shutdown_signals.py::{test_sigterm,test_sigint}_triggers_clean_exit_and_deregister`.

**Proven pre-existing and unrelated to M3** — not grounds to block:
- The failure is `_wait_for_registry_entry` timing out at 15s (a spawned `python -m claude_crew.cli` subprocess not registering in time) — a host-load-sensitive startup race, the exact flake class documented in CLAUDE.md.
- M3 touches only `shapes.py`, `server.py` (a tool definition inside `make_server`), and two new test files — nothing in `main()`, CLI, signal handling, or the instance registry that this test exercises.
- All other server/UI/dashboard tests that spawn servers and bind ports passed; a broken server start would have failed those en masse.
- **Decisive check:** ran the two tests on the base commit (`d9c15ad~1`, before any M3 work) in a throwaway worktree — they fail identically (`2 failed in 30.13s`). Confirmed pre-existing, not an M3 regression. (Coordinator's own clean-host run had the full suite at 1613 passed, consistent with intermittency.)

## Architecture alignment
M3's design — reuse the M1.5 gate verbatim, `broker.py` untouched, `register_proposal(shape, adaptation_diff?)` as the single proposal entry — matches `doc/ARCHITECTURE.md:56` and the M0/M1.5 proposal state machine documented at 52-63. No contradiction. The doc does not yet describe the `adapt_shape` tool or the `shapes.py` adaptation algebra; updating it is the documenter's retrospecting task, not a feature-review blocker.

## Verdict
No Critical or High findings. Integration seam is coherent, all 35 ATs are satisfied end-to-end, the gate and pre-instantiation guards are reused correctly, and the only suite failures are a proven pre-existing environmental flake. Synthesis is sound.
