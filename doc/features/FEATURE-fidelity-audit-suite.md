# Feature: Fidelity Audit Live-Test Suite

**Status**: Shipped (2026-05-16)
**Created**: 2026-05-16
**Vision row**: #27 (M, capabilities #1, #4)

---

## Problem

claude-crew's named differentiator is CLI fidelity — teammates obey Claude Code's rules (CLAUDE.md, skills, hooks, permission modes, MCP, plugins, agent-format YAML polymorphism). Today that moat is closed reactively. The 2026-05-07 bundled-pack dispatch regression cleared all 978 unit tests with zero signal because no test asserted the fidelity claim end-to-end against a real SDK subprocess.

This feature converts each named fidelity claim into a live-gated assertion: one test class per claim under `CLAUDE_CREW_LIVE_TESTS=1`, so the next time a claim erodes, a developer's local live run (or a scheduled CI job) fails loudly with a named claim rather than a silent regression caught months later by a user.

## What Shipped

Two new test modules, following the pattern from `tests/test_live_sdk.py` and
`tests/test_format_compat_e2e.py::TestLiveSdkToolsEmptyEnforcement`:

| Module | Gate | LOC | Classes |
|---|---|---|---|
| `tests/test_fidelity_audit.py` | `CLAUDE_CREW_LIVE_TESTS=1` (module-level `pytestmark`) | 1,091 | 9 |
| `tests/test_fidelity_audit_frontmatter.py` | none — runs in default CI | 154 | 1 |

No production code changes. One surgical fix: `_preserve_sdk_auth(tmp_home)` helper (added
to `tests/test_fidelity_audit.py`, ~25 lines) copies `~/.claude/.credentials.json` and
`~/.claude.json` into a tmp HOME so SDK subprocess tests that override `HOME` can still
authenticate. `_REAL_HOME` captured at module-import time to survive the monkeypatch scope.

### Fidelity Claims Covered

| Class | Claim | Mechanism |
|---|---|---|
| `TestBundledPackDispatchFidelity` | Bundled-pack subagents execute their bundled prompt, not a fabrication | Parent spawns with bundled pack; dispatches Task to bundled subagent; asserts sentinel substring in parent reply |
| `TestSkillDiscoveryFidelity` | User skills under tmp `~/.claude/skills/` are invocable inside an SDK teammate | tmp-HOME override with one skill containing a unique token; assert token appears in teammate reply |
| `TestHookFiringFidelity` | PreToolUse/PostToolUse Python-callable hooks fire inside SDK teammate; shell env-var carve-out (`CLAUDE_TOOL_NAME` / `CLAUDE_HOOK_EVENT` empty) is an asserted invariant | Hook writes sentinel file on tool fire; separate method captures `os.environ.get("CLAUDE_TOOL_NAME", "<EMPTY>")` and asserts `"<EMPTY>"` — failure means upstream closed the carve-out (requires CLAUDE.md update, not a regression fix) |
| `TestPluginScopeFidelity` | Plugin agents resolve inside teammates | tmp plugin dir under `~/.claude/plugins/cache/fidelity-plugin-probe-<uuid>/`; sentinel in agent prompt; assert dispatch and sentinel in reply |
| `TestMcpResolutionFidelity` | User-level `~/.claude.json` MCP servers are reachable from a teammate session | knowledge-graph MCP tool call; `pytest.skip` if server not registered (matches `_has_kg_server` pattern from `test_live_sdk.py`) |
| `TestAgentFormatYamlPolymorphism` | Both md-frontmatter and pure-YAML pack entries are dispatchable | Both formats instantiated as `AgentDefinition`; dispatched in a single live turn; both sentinels in reply. Note: YAML side bypasses `build_merged_pack` (see Known Gaps) |
| `TestFrontmatterNormalization` | Unix LF frontmatter loads cleanly; Windows CRLF is a documented xfail | `parse_pack_text` called directly with `\n` and `\r\n` variants; LF passes; CRLF `xfail(strict=True, reason="Windows CRLF — BACKLOG: frontmatter normalization fix")` |
| `TestAuthFailureSurface` | Auth-shaped SDK errors propagate cleanly to the lead | `monkeypatch.setattr(ClaudeSDKClient, "query", ...)` raises fabricated `RuntimeError("auth failure: simulated…")`; assert error envelope propagates with `"auth"` substring in payload |
| Cost autouse fixture | Per-test JSONL artifact | `tests/_artifacts/fidelity-audit-cost.jsonl` (gitignored via `tests/_artifacts/`); one line per test with `{test_id, input_tokens, output_tokens, cost_usd, wall_seconds}` |

### Test Commands

Default CI (gated-skip + loader tests; no API spend):

```bash
uv sync --frozen && uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py -v
```

Live run (developer-invoked; asserts real claims):

```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py -v --durations=10
```

## Validation Result (Cycle 0)

```
10 passed, 1 xfailed in 72.13s
```

All 7 live fidelity classes + `TestAuthFailureSurface` + `TestFrontmatterNormalization::test_unix_lf`
passed. `test_windows_crlf` xfailed strict as expected. Total cost: ~$0.35.

**Validation deviation (`validation.process.surgical-fix`):** First run failed — three tests
(`TestSkillDiscoveryFidelity`, `TestPluginScopeFidelity`, `TestAgentFormatYamlPolymorphism`)
returned `"Not logged in"` because `monkeypatch.setenv("HOME", tmp_path)` strips SDK subprocess
credentials. Fixed via `_preserve_sdk_auth(tmp_home)` + `_REAL_HOME` module-level capture.
Spec Assumption "HOME-override pattern is verified by existing live tests" cited
`tests/test_user_loader_live.py` which loads user-level config but does not authenticate an SDK
turn — the cited prior art did not cover the auth path.

## Known Gaps (non-blocking at ship)

| Severity | Tag | Note |
|---|---|---|
| ~~Medium~~ **CLOSED 2026-05-16** | `spec-satisfaction.yaml-loader-bypass` | ~~`TestAgentFormatYamlPolymorphism` manually constructs the YAML-side `AgentDefinition`; `discover_dir` globs `*.md` only so the loader never sees the file. AT8 asserts dispatch, not loader YAML support.~~ Closed by `fidelity-audit-followups` slice: `discover_dir` now globs `*.yaml`/`*.yml` alongside `*.md`; `parse_yaml_pack_text` added in `_loader.py`; AT8 refactored to route end-to-end through `build_merged_pack` (no manual `yaml.safe_load`). Re-validated 10p+1xfail in 72s. |
| ~~Medium~~ **CLOSED 2026-05-16** | `spec-satisfaction.cost-telemetry-zero` | ~~Autouse cost fixture skeleton present; no live test body populates `_test_cost_data` with `ResultMessage.usage`; artifact fields are present but all-zeros.~~ Closed by `fidelity-audit-followups` slice: `_record_sdk_cost(broker, tid, *, result_msg=None)` helper added; 5 broker-backed classes call it with `(broker, tid)`, 2 hook classes call it with `result_msg=...`. All 7 non-auth live classes now write real `ResultMessage.usage` data. Verified 7/9 non-zero cost lines per validation run. |
| Info | `cracks.hook-test-bypasses-sdkteammate` | `TestHookFiringFidelity` uses `ClaudeSDKClient` directly because `SdkTeammate._run` hard-codes its own telemetry hooks. Asserts SDK hook mechanism fires; does not assert operator-injectable hooks on `SdkTeammate` (no such surface exists today). |

## BACKLOG Closed

- **[2026-05-08] Live integration test for bundled-pack dispatch** — subsumed by
  `TestBundledPackDispatchFidelity`. The class docstring references the BACKLOG entry.
  Marked archived on merge.
