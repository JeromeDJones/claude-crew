# Breakout: fidelity-audit-followups

## Goal

Close two semantic gaps in the #27 fidelity-audit suite: (1) wire real `ResultMessage.usage` token + cost data into the per-test JSONL artifact so the cost record is no longer all-zeros, and (2) extend `discover_dir` to glob `*.yaml`/`*.yml` agent files so `TestAgentFormatYamlPolymorphism` exercises loader-side YAML support end-to-end through `build_merged_pack`, not just dispatch. Two localized changes, no cross-module reshape; live-gated tests stay live-gated and default-CI keeps skipping cleanly.

## Tasks

```yaml
tasks:
  - name: yaml-loader-extension
    description: |
      Extend `claude_crew/subagents/_user_loader.py::discover_dir` to glob
      `*.yaml`/`*.yml` alongside `*.md` (README exclusion, size/count caps,
      alphabetical sort, intra-dir kebab-key collision logic all preserved).
      Add a `parse_yaml_pack_file` (or equivalent suffix-dispatch in
      `claude_crew/subagents/_loader.py`) that treats the YAML doc as the
      frontmatter mapping, pulls `prompt_body` as the body, and reuses the
      existing `_validate_frontmatter` + `AgentDefinition` construction path.
      Reject missing `prompt_body` and empty body with `PackLoadError`.
      Add `tests/test_user_loader.py` cases for `.yaml`/`.yml` discovery,
      malformed-YAML WARN-and-skip, cross-format kebab-key collision (`.md`
      vs `.yaml`), and bundled-pack non-regression.
    dependsOn: []
    acceptanceTests: [3, 4, 6, 7]
    taskTouches:
      - "claude_crew/subagents/_loader.py"
      - "claude_crew/subagents/_user_loader.py"
      - "tests/test_user_loader.py"
    implementationKind: behavior-change

  - name: live-test-cost-and-yaml-dispatch
    description: |
      Add a `_record_sdk_cost(broker, tid)` helper to
      `tests/test_fidelity_audit.py` that reads
      `total_input_tokens`/`total_output_tokens`/`total_cost_usd` from
      `SdkTeammate.status_snapshot()` and populates the module-global
      `_test_cost_data` dict the autouse fixture already consumes. Call the
      helper from all 7 non-auth live test classes after the reply envelope
      arrives: `TestBundledPackDispatchFidelity`, `TestSkillDiscoveryFidelity`,
      both `TestHookFiringFidelity` cases, `TestPluginScopeFidelity`,
      `TestMcpResolutionFidelity`, and `TestAgentFormatYamlPolymorphism`.
      Helper must no-op silently on a tombstoned teammate.
      `TestAuthFailureSurface` is not touched. Fixture wire format at
      lines 87-131 is unchanged.

      In the same task, refactor
      `TestAgentFormatYamlPolymorphism::test_both_formats_dispatchable` to
      drop the manual `yaml.safe_load` + inline `AgentDefinition`
      construction and assert `agent_yaml_name in merged_pack` directly
      after `build_merged_pack` returns. Verify under live mode that both
      sentinels still appear in the parent reply (no dispatch regression)
      and that the test contributes a non-zero cost line. The refactor
      requires `yaml-loader-extension` because `discover_dir` must already
      glob `*.yaml` for the YAML agent to land in `merged_pack`.
    dependsOn: [yaml-loader-extension]
    acceptanceTests: [1, 5]
    taskTouches:
      - "tests/test_fidelity_audit.py"
    implementationKind: behavior-change

  - name: full-validation-baseline
    description: |
      Run the full live validation command from the spec
      (`rm -f tests/_artifacts/fidelity-audit-cost.jsonl && CLAUDE_CREW_LIVE_TESTS=1
      uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py -v`)
      and the default-CI command. Assert: 10 passed + 1 xfailed (matches #27
      baseline, AT-2), default-CI skip-clean (AT-8), and ≥7 cost lines have
      non-zero input_tokens/output_tokens/cost_usd (re-checks AT-1 end-to-end).
      No code is shipped from this task — it is the integration-validation
      gate that proves the prior two tasks compose. If a regression
      surfaces, escalate; do not amend tests in this task.
    dependsOn: [live-test-cost-and-yaml-dispatch, yaml-loader-extension]
    acceptanceTests: [2, 8]
    taskTouches:
      - ".rr/**"
    implementationKind: behavior-change
```

## Risks

- **`SdkTeammate.status_snapshot` field names drift** — task assumes `total_input_tokens`/`total_output_tokens`/`total_cost_usd` per sdk_teammate.py:922-924. If those names were renamed since #27, the helper fails silently (zeros). Implementor verifies by reading the snapshot field names before writing the helper, not after.
- **YAML body-key choice (`prompt_body`)** — codified in spec; bundled pack remains `.md`-only. Operators authoring new `.yaml` agents must use `prompt_body`. Risk of operator confusion is mitigated by an explicit `PackLoadError` naming the expected field when `prompt`-only is supplied.
- **Cross-format collision semantics** (`probe.md` + `probe.yaml`) — alphabetically-later wins (`.yaml` > `.md`). If operators ship both forms expecting markdown-precedence, surprises follow. Mitigated by the existing WARN message, which now must name both paths across formats. Validated by AT-7.
- **Cost capture timing** — `status_snapshot` must be read after the reply envelope but before the teammate is killed by `broker.shutdown_all()` in the fixture teardown. The helper is called inside the test body, which is the correct window; documented in the helper docstring.
- **`full-validation-baseline` task touches `.rr/**` only** — it is a gate, not a code change. If the gate fails, the failure routes back to whichever upstream task produced the regression, not to this task. The implementor for this task is expected to escalate cleanly, not patch.
- **Live spend variance** — ~$0.35 baseline can drift ±50% across runs. Validation accepts ≤2× budget; sustained drift outside that should re-open the cost spec, not be papered over.
- **Combined task scope** — `live-test-cost-and-yaml-dispatch` does two things (cost wiring across 7 classes + AT8 refactor) that share a file. Kept together because splitting them re-introduces the cycle-1 Invariant-2 violation (helper-only task with no AT). The slice reviewer should see both observables (≥7 non-zero cost lines, end-to-end YAML dispatch) land in the same diff.

## Notes

### Architecture conjunct → task mapping

The spec's Architecture Overview names two conjuncts; each is folded into exactly one task:

- **Cost telemetry** (snapshot-driven, no SDK-module changes) → covered by `live-test-cost-and-yaml-dispatch`. AT-1 lives here.
- **YAML loader** (`discover_dir` glob extension, `parse_yaml_pack_file`, collision/cap semantics) → covered by `yaml-loader-extension`.
- `full-validation-baseline` is the terminal integration gate covering AT-2 (#27 baseline preserved) and AT-8 (default-CI clean skip); it is not a separate architecture conjunct.

### DAG shape

- Three tasks, three edges: `yaml-loader-extension` is the root; `live-test-cost-and-yaml-dispatch` depends on it (the AT8 refactor needs the loader extension to land first); `full-validation-baseline` depends on both predecessors as the terminal gate.
- Alternate decomposition considered: split cost-wiring from the AT8 refactor into a dedicated scaffolding task. Rejected — the breakout schema rejects an `implementationKind: scaffolding` value, and the only other split shape (helper-only task with empty `acceptanceTests`) violates Invariant 2. Merging the two into one `behavior-change` task is the smallest legal decomposition that preserves AT-1 ownership integrity.
- Alternate considered: include `TestAuthFailureSurface` in the cost-wiring. Rejected per spec — `query` is monkeypatched before any network I/O, so cost is structurally 0.0; touching it adds no signal.
- `taskTouches` for `full-validation-baseline` is scoped to `.rr/**` to allow the implementor to write a validation log/report; no source-tree edits are permitted from this task.
