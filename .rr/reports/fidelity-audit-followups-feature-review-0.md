# Feature Review: fidelity-audit-followups

**Cycle:** 0
**Verdict:** PASS
**Tasks reviewed:** `yaml-loader-extension`, `live-test-cost-and-yaml-dispatch`, `full-validation-baseline` (all slice-review PASS)

---

## Check 1 — Cross-slice integration coherence

The DAG has one real seam (`yaml-loader-extension` → `live-test-cost-and-yaml-dispatch`) and one validation seam (both → `full-validation-baseline`). Both hold.

### Seam A: YAML loader feeds AT8 refactor end-to-end

Traced the path:

1. `claude_crew/subagents/_loader.py::parse_yaml_pack_file` — entire YAML doc is the frontmatter mapping, `prompt_body` is the body, reuses `_validate_frontmatter` + `build_subagent_prompt` so the resulting `AgentDefinition` is shape-identical to the `.md` path (substrate-prefix prompt, same field mapping).
2. `claude_crew/subagents/_user_loader.py::strict_parse` — suffix dispatch on `.yaml`/`.yml`, extras-check fires against `_ACCEPTED_FRONTMATTER_KEYS`, returns the same `(key, agent, settingSources, body)` tuple as the `.md` branch.
3. `discover_dir` — `itertools.chain(*.md, *.yaml, *.yml)` with README exclusion, size/count caps, and alphabetical sort preserved across the combined set.
4. `tests/test_fidelity_audit.py::TestAgentFormatYamlPolymorphism` — `agent_yaml_name in merged_pack` asserts *before* spawn, so a regression in `discover_dir`'s glob flips the test correctly. The `yaml.safe_load`+inline-`AgentDefinition` block is fully removed; remaining `yaml.dump` writes the fixture file (legitimate).

The contract holds: AT8 now tests loader + dispatch, not just dispatch.

### Seam B: `_record_sdk_cost` helper rides `status_snapshot`

Helper reads `broker.get_teammate_status(tid)` which internally calls `SdkTeammate.status_snapshot()` for alive teammates. Field names verified against `sdk_teammate.py:922-924` at slice review (`total_input_tokens` / `total_output_tokens` / `total_cost_usd`). Each test spawns a fresh teammate, so session-cumulative equals turn-delta (spec design decision honored).

Tombstone-safety: helper checks `"error" in status` and `not status.get("alive", False)` before reading — the broker returns an error key for unknown teammates and `alive=False` for tombstoned ones. The broad `except Exception` at the tail is documented telemetry-defensiveness with `noqa: BLE001`. Test failures surface through the assertion path, not through cost-capture exceptions.

### Seam C: Validation gate

`full-validation-baseline` is a `.rr/**`-only task. Cycle 1 ran clean: 10 pass + 1 xfail (matches #27 baseline), 7/9 non-zero cost lines, default-CI skip-clean, 78s wallclock. Cycle 0's hex-relay truncation was a pre-existing LLM brittleness (the relayed 30-char hex dropped a trailing char) and re-ran clean on cycle 1; not a slice regression. Cost-line population was independently verified on cycle 0 too, so the cost wiring is decoupled from the relay flake.

---

## Check 2 — Holistic spec satisfaction

| AT | Owner task | Verification | Status |
|----|------------|--------------|--------|
| AT-1 (≥7 non-zero cost lines) | live-test-cost | Live run cycle 1: 7/9 non-zero (auth-failure + xfail excluded) | ✓ |
| AT-2 (10 pass + 1 xfail baseline) | full-validation | Live run cycle 1: 10 passed + 1 xfailed, exit 0 | ✓ |
| AT-3 (`.yaml`/`.yml` discovery) | yaml-loader | `TestYamlDiscovery` (4 cases) PASS | ✓ |
| AT-4 (malformed YAML WARN+skip) | yaml-loader | `TestYamlMalformed` (3 cases) PASS | ✓ |
| AT-5 (AT8 end-to-end via build_merged_pack) | live-test-cost | `yaml.safe_load`+inline-`AgentDefinition` removed; `agent_yaml_name in merged_pack` asserted; both sentinels surface in live reply | ✓ |
| AT-6 (markdown non-regression) | yaml-loader | `TestMarkdownNonRegression` (4 cases) PASS; default-CI baseline 79p/9s/1xfail preserved | ✓ |
| AT-7 (cross-format kebab collision) | yaml-loader | `TestYamlKebabCollision` (`.yaml > .md`, `.yml > .md`) PASS | ✓ |
| AT-8 (default-CI clean skip) | full-validation | Cycle 1: 1 pass + 9 skipped + 1 xfailed, exit 0 | ✓ |

All 8 ATs are satisfied by the integrated diff. No silent over-claims: AT-1's "≥7 non-zero" traces cleanly from `_record_sdk_cost` → broker → `SdkTeammate.status_snapshot()` → the pre-existing F14 chokepoint that extracts from real `ResultMessage.usage`. The live artifact at cycle 1 confirms the data flows.

Spec design decisions all honored:
- Per-test cost rides `status_snapshot`, no new SDK hook ✓
- `_test_cost_data` wire format unchanged (lines 87-131 fixture untouched) ✓
- `prompt_body` is the canonical YAML body key ✓
- `.yaml` AND `.yml` both globbed ✓
- `TestAuthFailureSurface` not modified ✓
- Live-gate stays ✓

Out-of-scope items respected: no fixture refactor to `yield results`; no plugin-dir YAML; no markdown-frontmatter YAML extension; no live-budget enforcement.

---

## Check 3 — Cracks-fell-through

### Critical
_None._

### High
_None._

### Medium

- **[Med-01] `feature.quality.duplication` — Double I/O + double parse in `strict_parse` YAML branch.** `_user_loader.py::strict_parse` reads the file, parses YAML into `doc`, builds `fm_dict`, runs the extras-check — then calls `parse_yaml_pack_file(path)` which reads and parses the same file a second time. The markdown branch avoids this by passing already-read `text` to `parse_pack_text(text, path)`. Asymmetric design, negligible runtime cost, but a maintenance trap: any future per-file invariant (size cap, content check, error message refinement) has to be duplicated. Fix path: extract `_parse_yaml_doc(path) -> tuple[dict, str]` or add a `parse_yaml_pack_doc(doc, path)` sibling that accepts a pre-parsed mapping. Slice-review flagged the same finding; surfaced here for retro/follow-up. Category: `fix-style`.

- **[Med-02] `feature.quality.duplication` — Two parallel cost-extraction paths in `test_fidelity_audit.py`.** Five live classes use `_record_sdk_cost(broker, tid)` (snapshot-driven). Two `TestHookFiringFidelity` cases inline their own `ResultMessage.usage` extraction (lines 589-615 and 663-686) with extra cache-token roll-up logic the helper omits (`cache_read_input_tokens` + `cache_creation_input_tokens` added into `input_tokens`). Result: the same JSONL field has two semantic definitions depending on which test wrote the row. Defensible in isolation (the hook tests bypass the broker), but a downstream consumer of `fidelity-audit-cost.jsonl` cannot assume uniform `input_tokens` semantics. Fix path: extend `_record_sdk_cost` to accept an optional `ResultMessage` parameter and unify the cache-token math, or document the divergence in the JSONL writer. Category: `fix-style`.

- **[Med-03] `feature.quality.style` — Pre-existing style smells in touched test file not cleaned.** `tests/test_fidelity_audit.py` line 198 still uses `asyncio.get_event_loop()` inside the `_spawn_and_ask` coroutine — the very function the slice modified by inserting `_record_sdk_cost(broker, tid)` at line 200. CLAUDE.md test conventions mandate `asyncio.get_running_loop()`. Inline imports at lines 953-955 inside `test_both_formats_dispatchable` (`yaml`, `sdk_factory`, `build_merged_pack`) likewise survived a refactor that removed two sibling inline imports. Implementor deferred both as "pre-existing" — CLAUDE.md "No Pre-existing Excuses" explicitly forbids that escape hatch. Surface for retro apply-fix or follow-up; one-line fixes each. Category: `fix-style`.

### Low

- **[Low-01] `feature.review-process.flaky-baseline` — AT-2 inherits hex-relay flake.** The cycle-0 failure was a 30-char hex sentinel truncated by one trailing char at the LLM relay boundary. Cycle-1 retry was clean. Inherited from #27 (`TestBundledPackDispatchFidelity` and now `TestAgentFormatYamlPolymorphism` both use 32-char hex sentinels). Recommendation: shorten to 8 hex chars or use URL-safe slugs. Tracked at slice level; record-for-retro. Category: `record-for-retro`.

- **[Low-02] `feature.review-process.deviation` — `rr-slice-reviewer` died twice on `full-validation-baseline`.** `invalid_response: model returned no text content` on consecutive runs; coordinator authored the slice-review report from the comprehensive build reports. Doesn't impact verdict (the underlying validation evidence is on disk and verifiable), but worth retro analysis: was the prompt size, the validation-only nature, or the all-`.rr/` diff a triggering condition? Category: `record-for-retro`.

### Info

- **[Info-01]** Helper docstring says "reads from `status_snapshot`" but the actual call is `broker.get_teammate_status(tid)`. Functionally identical (broker passes through to the live teammate) and broker indirection is the better choice (gives tombstone-safety for free). Docstring accuracy nit only.

- **[Info-02]** Implementor for `full-validation-baseline` correctly escalated/retried on cycle 0 rather than patching the flake out of scope. Process discipline held.

---

## Verdict

PASS. All 8 ATs satisfied by the integrated diff and verified end-to-end at cycle 1 (10p+1xfail live, default-CI skip-clean, 7/9 non-zero cost lines). Cross-task seams hold; no over-claims; no Critical or High findings. Three Mediums are quality/duplication/style cleanups suitable for retro apply-fix; two Lows are record-for-retro process notes.
