# Spec: fidelity-audit-followups

## Problem

The #27 fidelity-audit suite shipped 2026-05-16 with two semantic gaps that weaken the CLI-fidelity moat it was built to defend:

1. **`cost-telemetry-zero`** — the autouse cost fixture in `tests/test_fidelity_audit.py:87-131` writes a JSONL record per test to `tests/_artifacts/fidelity-audit-cost.jsonl`, but every live test stores `{input_tokens:0, output_tokens:0, cost_usd:0.0}`. AT11's field-presence check passes while the artifact's whole purpose — a real cost record of the fidelity probe — is defeated.
2. **`yaml-loader-bypass`** — `TestAgentFormatYamlPolymorphism` manually `yaml.safe_load`s its YAML probe file and constructs the `AgentDefinition` inline, because `claude_crew/subagents/_loader.py::discover_dir` globs `*.md` only. AT8's claim ("both formats load via `build_merged_pack`") is over-stated; a regression breaking YAML in the loader would not flip the test.

Closing both gaps tightens existing fidelity claims without growing the surface — the artifact records real SDK spend, and AT8 asserts loader-side YAML support, not just dispatch-side.

## Architecture Overview

Two localized changes, no cross-module reshape:

- **Cost telemetry** — Each live test class spawns a fresh `SdkTeammate` via `sdk_factory`. `SdkTeammate.status_snapshot()` already exposes session-cumulative `total_input_tokens`, `total_output_tokens`, and `total_cost_usd` (populated from `ResultMessage.usage` / `.total_cost_usd` per the existing F14 chokepoint at `sdk_teammate.py:1170-1181`). Because each test spawns a fresh teammate, the session-cumulative equals the turn delta — no per-turn extraction needed inside the SDK module. The plumbing change is in the tests: after the reply envelope arrives, fetch the snapshot from the broker, copy the three fields into the module-global `_test_cost_data` dict that the autouse fixture already consumes. Helper added to `tests/test_fidelity_audit.py` to keep each test body changed minimally.
- **YAML loader** — `discover_dir` in `claude_crew/subagents/_user_loader.py` extends its glob to include `*.yaml` and `*.yml` alongside `*.md`. The existing `parse_pack_text` already speaks YAML-frontmatter-with-body — a pure-`.yaml` file with `description`, `model`, `tools`, and a body field needs a new entry point. Per AT8's existing fixture shape (`description`, `model`, `tools`, `prompt_body`), the implementor adds a sibling `parse_yaml_pack_file` (or extends `parse_pack_file` with a format dispatch) that treats the entire YAML doc as the frontmatter equivalent, pulls `prompt_body` (or `prompt`) as the body, and otherwise reuses the existing `_validate_frontmatter` + `AgentDefinition` construction path. `TestAgentFormatYamlPolymorphism` is then refactored to drop its manual `yaml.safe_load` block and assert `agent_yaml_name in merged_pack` end-to-end through `build_merged_pack`.

## Data / API Contracts

New helper in `tests/test_fidelity_audit.py` (private to the module):

```python
def _record_sdk_cost(broker: Broker, tid: str) -> None:
    """Populate _test_cost_data from the teammate's status_snapshot.

    Call this from each live test body AFTER the reply envelope has arrived
    and BEFORE the test function returns (so the autouse fixture sees the
    populated dict on the post-yield branch).

    Reads `total_input_tokens`, `total_output_tokens`, `total_cost_usd` from
    `SdkTeammate.status_snapshot()` (already populated from ResultMessage
    per F14 in sdk_teammate.py). Skips silently if the teammate is gone
    (tombstoned) — the test will surface the real failure on its own.
    """
```

Loader extension in `claude_crew/subagents/_loader.py` (new public function, or extension to existing):

```python
def parse_yaml_pack_file(path: Path) -> tuple[str, AgentDefinition, PackFrontmatter, str]:
    """Parse a pure-YAML agent file into (key, AgentDefinition, frontmatter, body).

    The YAML document IS the frontmatter mapping. The body is read from a
    well-known field — `prompt_body` (matches AT8 fixture shape). All other
    fields share validation with `_validate_frontmatter`.
    """
```

`discover_dir` in `claude_crew/subagents/_user_loader.py` extends its candidate set:

```python
# Before: directory.glob("*.md")
# After:  union of *.md (existing path) and *.yaml/*.yml (new path).
#         README.md exclusion preserved; size/count caps still apply across
#         the combined set; alphabetical sort preserved for determinism.
```

## Design Decisions

- **Per-test cost capture rides `status_snapshot`, not a new SDK hook.** — *Rationale:* The snapshot fields already exist; each test spawns a fresh teammate so session-cumulative is the turn delta. Adds zero new state to `SdkTeammate`. — *Carried into:* `_record_sdk_cost` helper; AT-1 verifies non-zero `input_tokens`/`output_tokens`/`cost_usd` for at least 7 live tests.
- **`_test_cost_data` module-global wire format is preserved.** — *Rationale:* The task-0 debrief's "yield results" reshape is cleaner but violates the explicit "minimum diff" constraint in the idea. — *Carried into:* fixture body at `tests/test_fidelity_audit.py:106-131` unchanged; only its dict-population path changes.
- **YAML file body key is `prompt_body`.** — *Rationale:* Matches the existing AT8 fixture; no fixture rewrite needed; choosing `prompt` would clash with `AgentDefinition.prompt` semantics that already include the substrate prefix. — *Carried into:* `parse_yaml_pack_file`; AT8 test fixture; new loader unit test.
- **YAML glob extension adds `.yaml` AND `.yml`.** — *Rationale:* Both extensions are widely used; rejecting `.yml` would surprise operators. — *Carried into:* `discover_dir` candidate-set construction; new unit test asserting both extensions discover.
- **Auth-failure class is exempt from cost capture.** — *Rationale:* Idea explicitly excludes it; `query` is monkeypatched before any SDK round-trip so cost is structurally 0.0. — *Carried into:* `TestAuthFailureSurface` is not modified; the fixture's existing `0.0` defaults handle it.
- **Live-gate stays.** — *Rationale:* Both gaps validate only under real SDK calls; default CI runs must continue to skip cleanly. — *Carried into:* `pytestmark` at module top unchanged; new unit test for YAML loader runs default-CI (no SDK).

## Edge Cases

- **Teammate tombstoned before snapshot read.** `_record_sdk_cost` must not raise on a missing/dead teammate — log and skip; let the real test assertion surface the failure. The cost line will fall back to zeros for that test, which is acceptable telemetry.
- **`ResultMessage.usage` malformed/absent.** Already handled at the SDK boundary in `sdk_teammate.py:197-224` (logs and skips). Snapshot fields stay at their initial zero. AT-1 asserts non-zero on *at least 7* lines, not all 10 — gives slack for one anomalous run.
- **Multiple `ResultMessage`s per turn (D-6 overwrite).** Cumulative semantics; last-wins is already correct.
- **YAML file with `.md`-conflicting kebab-key.** Intra-dir collision warning is unchanged (alphabetically-later wins). New: a `.md` and `.yaml` with the same stem collide on the same kebab-key — emit the same WARN and apply the same alphabetical-later-wins rule (`.yaml` > `.md`).
- **Malformed YAML file** (missing `description`, invalid types). Reuses `_validate_frontmatter`; per-file parse errors emit WARN and skip, sibling files still load. Unchanged from existing `.md` behavior.
- **Empty `prompt_body`.** Mirrors empty-body `.md` rejection in `parse_pack_text`: raise `PackLoadError`.
- **YAML file with `prompt` instead of `prompt_body`.** Reject with a clear `PackLoadError` naming the expected field, rather than silently substituting — surfaces operator typos.
- **`.YAML` / `.YML` uppercase extensions on case-insensitive filesystems.** Match existing `*.md` behavior — lowercase-only glob; uppercase files silently skipped. Documented in the docstring.
- **Default-CI cost artifact absent or stale.** The fixture already lazily creates the directory and appends; under non-live mode no writes happen. Tests must not assume the file exists between live runs.

## Acceptance Tests

1. **AT-1 (cost-telemetry populated):** Given `CLAUDE_CREW_LIVE_TESTS=1` is set and `tests/_artifacts/fidelity-audit-cost.jsonl` is removed beforehand, when `uv run pytest tests/test_fidelity_audit.py -v` runs to completion, then the resulting JSONL file contains at least 7 lines where **all three** of `input_tokens > 0`, `output_tokens > 0`, and `cost_usd > 0.0` hold. The auth-failure test class line may remain at zeros (excluded).
2. **AT-2 (baseline preserved):** Given the same live run, the test result count remains **10 passed + 1 strict-xfail** matching the #27 ship baseline. No new failures, no formerly-passing tests flipped.
3. **AT-3 (YAML loader discovers `.yaml` and `.yml`):** Given a tmp `~/.claude/agents/` directory containing one `*.md`, one `*.yaml`, and one `*.yml` agent file each with distinct canonical names, when `discover_dir` runs, then the returned pack dict contains all three canonical keys.
4. **AT-4 (YAML loader rejects malformed):** Given a `*.yaml` agent file missing the required `description` field, when `discover_dir` runs, then the malformed file emits a WARN and is skipped, and sibling well-formed files still load.
5. **AT-5 (TestAgentFormatYamlPolymorphism end-to-end via `build_merged_pack`):** Given the refactored AT8 test, when the test body executes, then (a) the test contains no `yaml.safe_load` call constructing an `AgentDefinition`, (b) both `agent_md_name` and `agent_yaml_name` are present in the `merged_pack` returned by `build_merged_pack` *before* any manual augmentation, and (c) both sentinels appear in the parent reply under live mode.
6. **AT-6 (markdown discovery non-regression):** Given the existing bundled-pack `.md` files, when `load_default_pack()` runs, then the returned pack is byte-for-byte equivalent to the pre-change behavior (same keys, same `AgentDefinition` fields).
7. **AT-7 (kebab-key collision across formats):** Given a tmp agent dir containing both `probe.md` and `probe.yaml` declaring different bodies, when `discover_dir` runs, then a collision WARN is emitted naming both paths and the alphabetically-later file (`probe.yaml`) wins.
8. **AT-8 (default-CI clean skip):** Given `CLAUDE_CREW_LIVE_TESTS` is unset, when `uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py` runs, then live classes skip cleanly and the new YAML-loader unit tests (default-CI) pass.

## Test Command

Prerequisites: SDK auth required for AT-1, AT-2, AT-5 (live). The implementor's local environment already has `~/.claude/.credentials.json` (per #27's `_preserve_sdk_auth` helper). No new dependencies — `yaml` is already in `pyproject.toml` (used by `_loader.py`). Approximate spend: ~$0.35 for the live run (matches #27 baseline).

Default-CI gate (no SDK cost, runs unit tests + live-gated-skip):

```bash
uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py tests/test_user_loader.py -v
```

Full live validation (AT-1/AT-2/AT-5; ~$0.35 real spend):

```bash
rm -f tests/_artifacts/fidelity-audit-cost.jsonl && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py -v && python -c "import json,sys; lines=[json.loads(l) for l in open('tests/_artifacts/fidelity-audit-cost.jsonl')]; nonzero=[l for l in lines if l['input_tokens']>0 and l['output_tokens']>0 and l['cost_usd']>0.0]; print(f'nonzero cost lines: {len(nonzero)}/{len(lines)}'); sys.exit(0 if len(nonzero) >= 7 else 1)"
```

## Out of Scope

- Adding new fidelity claims (this slice closes gaps in existing claims, doesn't grow the surface).
- Refactoring the autouse fixture to a `yield results` per-test capture shape (cleaner but violates minimum-diff constraint).
- Touching `tests/test_fidelity_audit_frontmatter.py` — the xfail-strict Windows-CRLF frontmatter test is the documented gap, untouched.
- Extending YAML support to plugin agent dirs that have format-specific quirks; this slice ships parity with `.md` discovery only.
- Adding YAML support to the markdown-frontmatter `_split_frontmatter` path (pure-YAML is a sibling format, not a replacement).
- Live-budget enforcement (still informational per #27).

## Assumptions

- **Per-test cost via `status_snapshot` is the minimum-diff path.** — *Default:* Use snapshot fields rather than threading a per-turn callback through `SdkTeammate`. — *Rationale:* Snapshot already exposes the data and each test spawns a fresh teammate, so session totals == turn delta. Threading a callback would touch `sdk_teammate.py` and the broker spawn path for zero added information.
- **`prompt_body` is the canonical YAML body key.** — *Default:* Reuse AT8's existing fixture key. — *Rationale:* Matches the only existing fixture; `prompt` collides with `AgentDefinition.prompt` semantics (which already includes the substrate prefix). Operators can extend later if a different convention emerges.
- **`.yaml` discovery is folded into `discover_dir`, not a new sibling function.** — *Default:* Extend the existing function rather than build `discover_yaml_dir`. — *Rationale:* Callers (server.py:420, `load_user_agents`, `load_project_agents`, `_read_installed_plugins`) all want both formats; a sibling function would duplicate the collision/cap/sort logic.
- **Auth-failure class stays at zero cost.** — *Default:* No fixture changes for `TestAuthFailureSurface`. — *Rationale:* Idea explicitly excludes it; `query` is intercepted before network I/O so cost is structurally 0.0.
- **The bundled pack stays `.md`-only for now.** — *Default:* Do not convert any bundled agent to `.yaml`. — *Rationale:* AT-6 explicitly requires no-regression on markdown discovery; bundled pack stability is load-bearing for #15. YAML support is for operator-authored agents.
- **Default-CI test command in spec includes `tests/test_user_loader.py`.** — *Default:* Run it to catch loader regressions. — *Rationale:* The loader change has the broadest blast radius across that file.

## Open Questions

(none)

## Validation

After implementation lands and slice/feature review pass, the coordinator runs the full live command (above) and asserts:

```bash
rm -f tests/_artifacts/fidelity-audit-cost.jsonl && CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py -v
```

Pass criteria (all required):
- pytest summary reports `10 passed, 1 xfailed` (matches #27 baseline; no new failures).
- `tests/_artifacts/fidelity-audit-cost.jsonl` exists and contains ≥ 7 lines where `input_tokens > 0` AND `output_tokens > 0` AND `cost_usd > 0.0`.
- `grep -c "yaml.safe_load" tests/test_fidelity_audit.py` returns 0 inside the `TestAgentFormatYamlPolymorphism` class (the refactor dropped the manual parse).
- Total wall time within 2× the #27 baseline (~72s → ≤ 150s); total spend within 2× of $0.35 budget.

Fail routing: any criterion fails → feedback to planner + plan-reviewer + the implementor whose slice the validation hit.

## Design Notes

- **Pattern reference for `ResultMessage.usage` extraction.** Already implemented in `claude_crew/sdk_teammate.py::_collect_response_text` (lines 172-260). The new code does NOT re-extract — it reads the already-populated snapshot fields. This avoids parallel extraction paths.
- **Test conventions from #27 apply.** Imports at module top (not inside test bodies — `_record_sdk_cost` import goes in the existing import block); `asyncio.get_running_loop()` not `get_event_loop()`; bounded async drains via `asyncio.wait_for`; `_preserve_sdk_auth` for HOME-monkeypatch tests.
- **YAML loader-side test goes in `tests/test_user_loader.py`** — that's the canonical home for `discover_dir` tests (see existing `test_discover_dir_*` cases at line 643+).
- **Risk: cost-telemetry adds ~50–100ms per test for the snapshot read.** Negligible against 5–15s per live SDK turn. Worth a sanity check in validation.
- **Why not extend `parse_pack_file` to dispatch on suffix?** Acceptable alternative; the spec leaves the function shape to the implementor. The contract is "`discover_dir` produces correct `AgentDefinition`s for `.md`, `.yaml`, and `.yml` inputs."
