# Slice Review: fidelity-audit-followups task=live-test-cost-and-yaml-dispatch

**Cycle:** 0  
**Reviewer re-run:** `uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py tests/test_user_loader.py -v`  
**Result:** 79 passed, 9 skipped, 1 xfailed — exit 0 ✓

---

## Check 1 — Slice Adherence

### Scope (taskTouches)

Breakout declares `taskTouches: tests/test_fidelity_audit.py only`. Actual diff:

```
.rr/prompts/implementor-cycle0.md      # RR artifact — excluded
.rr/prompts/implementor-cycle0.vars    # RR artifact — excluded
tests/test_fidelity_audit.py           # in scope ✓
```

No out-of-scope source edits. ✓

### AT-1 — Cost helper exists, called in all 7 non-auth live classes

`_record_sdk_cost(broker: Broker, tid: str)` added at lines 268–294. Field names verified against `SdkTeammate.status_snapshot()` (`sdk_teammate.py` lines 922–924):

| Helper reads | `status_snapshot()` emits | Match |
|---|---|---|
| `total_input_tokens` | `total_input_tokens` | ✓ |
| `total_output_tokens` | `total_output_tokens` | ✓ |
| `total_cost_usd` | `total_cost_usd` | ✓ |

Coverage of the 7 non-auth live classes:

| Class | Mechanism | Call site |
|---|---|---|
| `TestBundledPackDispatchFidelity` | `_spawn_and_ask` | line 408 (inside helper) |
| `TestSkillDiscoveryFidelity` | `_spawn_and_ask` | line 511 (inside helper) |
| `TestHookFiringFidelity` (PreToolUse) | Direct `ResultMessage` drain | lines 600–612 |
| `TestHookFiringFidelity` (EnvCapture) | Direct `ResultMessage` drain | lines 663–686 |
| `TestPluginScopeFidelity` | `_spawn_and_ask` | line 839 (inside helper) |
| `TestMcpResolutionFidelity` | `_spawn_and_ask` | (via helper) |
| `TestAgentFormatYamlPolymorphism` | `_spawn_and_ask` | line 1060 |

`TestAuthFailureSurface`: exempt per spec design decision — confirmed absent. ✓

Note: `TestHookFiringFidelity` correctly extracts cost from `ResultMessage.usage` directly (it uses `ClaudeSDKClient` not `_spawn_and_ask`); the field mapping differs (`input_tokens` + cache fields vs. `total_input_tokens`). This is an intentional design difference because the hook tests bypass the broker/teammate path. Semantically coherent with the two code paths.

Full AT-1 verification against live artifact requires `CLAUDE_CREW_LIVE_TESTS=1` (Task 3's gate). Default-CI validation here confirms the helper is wired; non-zero value check deferred correctly.

### AT-5 — YAML dispatch through `build_merged_pack`

Removed from `test_both_formats_dispatchable`:
- `yaml.safe_load(yaml_file.read_text())` — manual YAML parse ✗ gone
- `AgentDefinition(...)` inline construction ✗ gone  
- `build_subagent_prompt` import ✗ gone
- `full_pack = {**merged_pack, agent_yaml_name: yaml_agent}` — manual merge ✗ gone

Replaced with:
```python
merged_pack, _role_ss, _bodies = build_merged_pack(home_dir=tmp_path, project_root=...)
assert agent_yaml_name in merged_pack, ...
# factory receives merged_pack directly (not full_pack)
```

`yaml.dump()` still used at line 1004 to write the fixture `.yaml` file — legitimate, not a parsing bypass. ✓

AT-5 adherence confirmed: the assertion now exercises `discover_dir` + `parse_yaml_pack_file` end-to-end. A regression in the YAML loader will flip this test correctly. ✓

---

## Check 2 — Non-Regression

Re-run result: **79 passed, 9 skipped, 1 xfailed — exit 0**. Matches build report exactly. No previously-green test turned red. ✓

---

## Check 3 — Code-Quality Smoke

### Critical

_None identified._

### High

_None identified._

### Medium

- [Medium-01] `slice.quality.style` — `tests/test_fidelity_audit.py` line 198: `asyncio.get_event_loop()` inside async coroutine `_spawn_and_ask`. This task added `_record_sdk_cost(broker, tid)` at line 203 inside this function; the `get_event_loop()` line was pre-existing and not introduced here. CLAUDE.md rule: "If you touched the file, leave it cleaner"; `asyncio.get_event_loop()` inside coroutines is deprecated since Python 3.10 (CLAUDE.md test convention `asyncio.get_running_loop()` required). The implementor deferred this as pre-existing. One-line fix: `loop = asyncio.get_running_loop()`. Category: `fix-style`. *(Note: `TestAgentFormatYamlPolymorphism.test_both_formats_dispatchable` at line 1045 correctly uses `asyncio.get_running_loop()` — inconsistency within the same file.)*

- [Medium-02] `slice.quality.style` — `tests/test_fidelity_audit.py` lines 954–957: inline imports inside `test_both_formats_dispatchable` (`import yaml`, `from claude_crew.factories import sdk_factory`, `from claude_crew.subagents._user_loader import build_merged_pack`) violate CLAUDE.md test convention "Imports at module top." These are pre-existing — this task removed two inline imports (`AgentDefinition`, `build_subagent_prompt`) without moving the survivors to module level. Category: `fix-style`.

### Low

- [Low-01] `slice.quality.swallowed-error` — `_record_sdk_cost` lines 293–294: `except Exception: pass` with `# noqa: BLE001`. The broad catch with silent pass is intentional test-telemetry defensiveness (documented in docstring; `noqa` annotation present). Acceptable for telemetry infrastructure where a capture failure must not fail the test. Noting for completeness.

### Info

- [Info-01] `slice.review-process.cross-slice-observation` — `TestHookFiringFidelity` cost extraction (lines 589–615 and 663–686) reads `ResultMessage.usage` as a dict (`_usage.get("input_tokens", 0)`) and `ResultMessage.total_cost_usd`. Per CLAUDE.md SDK invariant: "Token/cost telemetry rolls up at end-of-turn." For short single-turn hook tests this is fine; for multi-Task parent turns, token counts could be zero until `ResultMessage` arrives. The hook tests are single-turn, so this path is sound. The feature-reviewer should verify composite roll-up behavior if AT-2 baseline validation is sensitive to field shape.

---

## Summary

| Check | Result |
|---|---|
| Slice adherence (AT-1) | PASS |
| Slice adherence (AT-5) | PASS |
| Non-regression | PASS (79/0/9skip/1xfail) |
| Scope (taskTouches) | PASS |
| Code-quality smoke | 2 Mediums (pre-existing style deferred), 1 Low |

No Critical or High findings. Two Mediums are pre-existing style violations that the implementor deferred with a "pre-existing" label — CLAUDE.md explicitly forbids this excuse. Flagged for awareness; they do not block this slice.
