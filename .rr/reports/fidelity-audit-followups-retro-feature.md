# Feature Retro: fidelity-audit-followups

**Cycle:** 0
**Mode:** raw findings only (per user-in-the-loop retro design; no categorization, no destinations, no apply/defer split)
**Validation:** PASS — 10p + 1xfail in 76s, ~$0.35 spend; default-CI skip-clean.

---

## What Went Well

- **Slice composed cleanly end-to-end on the first integrated live run.** All 8 ATs verified at validation; no integration surprise between `yaml-loader-extension` and the AT8 refactor in `live-test-cost-and-yaml-dispatch`. Evidence: `feature-review-0.md` Check 1 Seam A; validation report.
- **Spec design discipline held — zero-diff to `sdk_teammate.py`.** `_record_sdk_cost` rode the pre-existing `status_snapshot` chokepoint instead of threading a new SDK callback. Spec called this out as the minimum-diff path; implementor honored it. Evidence: `spec.md` "Design Decisions" §1; `live-test-cost-and-yaml-dispatch-slice-review-0.md` Check 1 AT-1.
- **Field-name verification before writing the helper paid off.** Slice-review confirmed the helper reads `total_input_tokens` / `total_output_tokens` / `total_cost_usd` against `sdk_teammate.py:922-924` — the risk flagged in the breakout (`SdkTeammate.status_snapshot` field-names drift) was structurally checked, not assumed. Evidence: `breakouts/fidelity-audit-followups.md` Risks §1; `live-test-cost-and-yaml-dispatch-slice-review-0.md` Check 1 table.
- **Per-task debriefs transferred concrete pointers forward.** Task-0's debrief carried the `status_snapshot` field-name location into task-1's prompt; task-1's debrief noted the cycle-0 retry pattern that task-2 needed. Unusually rich for a 3-task slice. Evidence: `*-debrief-implementor-*.md`.
- **AT-1 cost wiring survived the cycle-0 flake.** Cost lines populated cleanly (7/9 non-zero) even on the cycle-0 run that hit the hex-relay truncation — proves the cost-capture path is decoupled from the assertion path. Evidence: `full-validation-baseline-slice-review-0.md` cycle-0 flake analysis.
- **Plan review passed in a single cycle.** No spec rework needed; the planner's spec was self-consistent on first audit. Evidence: `plan-review-0.md`.
- **No source diff from the validation-gate task.** `taskTouches: .rr/**` was honored — implementor escalated/retried the cycle-0 flake rather than patching it out of scope. Evidence: `full-validation-baseline-slice-review-0.md` Check 2.

---

## What Didn't

- **3 breakout-review cycles to reach PASS.** Cycle 0: AT-1 ownership drift between two tasks. Cycle 1: Invariant-2 violation (helper-only task with empty `acceptanceTests`). Cycle 2: schema mismatch (proposed `implementationKind: scaffolding`, not an accepted value). Evidence: `breakout-review-0.md`, `-1.md`, `-2.md`; `breakouts/fidelity-audit-followups.md` Notes §"Alternate decomposition considered".
- **Coordinator gave wrong shape advice on the breakout split.** The "split cost-wiring from AT8 refactor into a scaffolding task" suggestion turned out to be schema-illegal; the breakouter had to merge them back together (the eventual `live-test-cost-and-yaml-dispatch` two-observable task) — which the breakout notes explicitly call out as the only legal decomposition. Evidence: `breakouts/fidelity-audit-followups.md` Notes §"DAG shape".
- **Task-2 hex-relay flake forced a cycle-1 retry.** A 30-char hex sentinel relayed through the LLM lost one trailing char in `TestBundledPackDispatchFidelity` — a pre-existing structural fragility in #27's sentinel design that this slice did not introduce but did inherit. Cost: an extra ~$0.35 live run. Evidence: `full-validation-baseline-build-0.md`, `-1.md`; `full-validation-baseline-slice-review-0.md` cycle-0 flake analysis.
- **`rr-slice-reviewer` died twice consecutively on task-2.** `invalid_response: model returned no text content` on back-to-back invocations of the validation-only slice. Coordinator wrote the slice-review report from the build evidence. Logged to `~/dev/FDE/doc/BACKLOG.md` `[2026-05-16] Per-task slice-reviewer lifecycle change`. Evidence: `full-validation-baseline-slice-review-0.md` preamble; deviation `slice-review.process.coordinator-authored`.
- **2 pre-existing style smells survived the slice unchanged despite touching the function.** `asyncio.get_event_loop()` at `tests/test_fidelity_audit.py:198` — inside `_spawn_and_ask`, the very function the slice modified at line 200 to insert `_record_sdk_cost`. Inline imports at lines 953-955 in `test_both_formats_dispatchable` survived a refactor that explicitly removed two sibling inline imports. Implementor deferred both as "pre-existing"; CLAUDE.md "No Pre-existing Excuses" forbids that label. Evidence: `live-test-cost-and-yaml-dispatch-slice-review-0.md` Med-01, Med-02; `feature-review-0.md` Med-03.
- **`strict_parse` YAML branch double-reads and double-parses the file.** The branch reads, parses, runs the extras-check on `fm_dict`, then calls `parse_yaml_pack_file(path)` which reads + parses again. The markdown branch threads `text` through — asymmetric design. Negligible perf cost, real maintenance trap. Surfaced at slice AND feature review. Evidence: `yaml-loader-extension-slice-review-0.md` Med-01; `feature-review-0.md` Med-01.
- **Two parallel cost-extraction paths in `test_fidelity_audit.py`.** Five live classes use `_record_sdk_cost(broker, tid)` (snapshot-driven, ignores cache tokens). Two `TestHookFiringFidelity` cases inline their own `ResultMessage.usage` extraction with `cache_read_input_tokens + cache_creation_input_tokens` rolled into `input_tokens`. Same JSONL field, two semantic definitions. Defensible (hook tests bypass the broker), but a downstream consumer cannot assume uniform `input_tokens` semantics. Evidence: `feature-review-0.md` Med-02.
- **Coordinator workflow workarounds:** had to use `git add -f` for `.rr/` paths because this repo's gitignore excludes them (breaks the audit-trail commit promise); had to `jq` directly into the state file to set the per-task implementor map because `state-op.sh` has no setter. Evidence: deviation log; `~/dev/FDE/doc/BACKLOG.md` `[2026-05-16]` entries.

---

## Surprises

- **The flake fired on the cycle that already had a non-trivial integration to verify.** Cycle 0 of task-2's validation was supposed to be the clean "everything composes" run; instead it surfaced the pre-existing hex-relay brittleness. The signal was correct (cost wiring + AT8 dispatch both worked), but the visible result was a red bar — a routing trap if the operator hadn't read the assertion text.
- **Cost-capture in `TestHookFiringFidelity` ended up structurally different from the helper.** Spec presented a single unified mechanism via `_record_sdk_cost`. Implementor reasonably chose inline extraction for the two classes that use `ClaudeSDKClient` directly (no broker/teammate path to query) — but no one anticipated this divergence in the spec or the breakout. The fix path is small; the surprise is that the spec named "one helper" and reality needed "two paths".
- **`rr-slice-reviewer` failing on a validation-only diff was unexpected.** The task with the smallest review surface (no source code) was the one the reviewer choked on. Suggests the failure mode is upstream of complexity — possibly prompt structure or context-window-related for that specific role.
- **Plan review one-shotted but breakout review took three cycles.** The harder spec quality bar passed first; the more mechanical DAG-shaping rules tripped up multiple cycles. Suggests the breakout schema's invariants (especially Invariant 2 and the `implementationKind` enum) are not well-internalized by the breakouter on first pass.

---

## Lessons for Future Slices

- **Pre-existing style smells in a touched function are this-slice's problem.** "Pre-existing" as a deferral label is explicitly out per CLAUDE.md. If implementor prompts named the file's known smells (one-line fixes for `get_event_loop`, inline imports), they'd land in the same diff. Pattern matches the #27 retro's "scaffold-task-sets-the-norm" lesson — surface known smells in the implementor prompt, not in slice-review feedback.
- **Hex sentinels at LLM relay boundaries are structurally flaky above ~16 chars.** Two classes now use 32-char hex (`TestBundledPackDispatchFidelity`, `TestAgentFormatYamlPolymorphism`). Shorten to 8 hex or switch to URL-safe slugs. Tracked but not yet acted on.
- **Loader-format symmetry should be expressed at the parser interface.** The `parse_pack_text(text, path)` vs `parse_yaml_pack_file(path)` shape asymmetry is what allowed the `strict_parse` double-I/O. Future format additions should standardize on `parse_X(text_or_doc, path)`, with `strict_parse` handling I/O once.
- **A validation-only task is still worth a real slice-reviewer.** When the role hangs, the coordinator-authored substitute is functional but loses the second-pair-of-eyes signal. Worth investigating the slice-reviewer failure mode (logged to `~/dev/FDE/doc/BACKLOG.md`) — possibly per-task spawn + Haiku-delegated reads.
- **Cost-extraction paths should be unified at the helper boundary.** Extend `_record_sdk_cost` to accept an optional `ResultMessage` so the two `TestHookFiringFidelity` classes route through the same function. Same JSONL semantics for all cost rows is the artifact's whole purpose.
- **Breakout-schema invariants need pre-emptive documentation in the breakout prompt.** Three cycles to land a 3-task DAG suggests the breakouter doesn't have Invariant 2 and the `implementationKind` enum salient at draft time. Cite them in the prompt or in a checklist the breakouter walks before submitting.
- **Coordinator workflow gaps from #27 are still gaps.** `git add -f` for `.rr/` paths and the missing `state-op.sh` per-task implementor-map setter both bit again. Already in BACKLOG; flagged so they don't slip.
- **Total spend ~$1.05** ($0.35 task-2 cycle-0 + $0.35 task-2 cycle-1 + $0.35 validation) — 3× the spec's $0.35 single-run budget because of the cycle-0 flake retry and the separate validation-phase run. Worth tracking per-slice in retros as a structural signal: more retries = more spend.

---

---

## Routing

User triage applied 2026-05-16. Three buckets:

**Apply-in-feature (shipped in `00bdd74` after retro):**
- A: shorten LLM-relayed hex sentinels (32→12 hex) in `TestBundledPackDispatchFidelity`, `TestAgentFormatYamlPolymorphism` → `tests/test_fidelity_audit.py`
- B: `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (3 sites); hoist inline imports from `test_both_formats_dispatchable` to module top → `tests/test_fidelity_audit.py`
- C+E: rename `parse_yaml_pack_file(path)` → `parse_yaml_pack_text(text, path)`; `strict_parse` reads YAML once and threads text → `claude_crew/subagents/_loader.py`, `claude_crew/subagents/_user_loader.py`, `tests/test_fidelity_audit.py`
- D: overload `_record_sdk_cost(broker, tid, *, result_msg=None)` so all 7 live classes write through one helper → `tests/test_fidelity_audit.py`

**Already routed (no new destination):**
- 3 breakout-review cycles / coordinator wrong-advice / `rr-slice-reviewer` death → captured in `~/dev/FDE/doc/BACKLOG.md d011586` (per-task slice-reviewer + Haiku-delegated reads)
- `git add -f .rr/` workaround / `state-op.sh` per-task implementor map setter → already in `~/dev/FDE/doc/BACKLOG.md [2026-05-16]` from #27 dogfood
- Breakout-schema invariant documentation in breakout prompt → covered by `prior-task slice-review digest` entry in same FDE BACKLOG section

**Captured in retro itself (discard for routing):**
- Total spend ~$1.05 observation
- 3-cycle breakout / 1-cycle plan-review surprise observation
- Cost-path divergence-not-anticipated observation (now resolved by D)
