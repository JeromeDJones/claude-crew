## Task

Decompose the following idea into a slice spec. Write the spec to:
`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md`

The spec must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt).

## Idea

Close two semantic gaps in the #27 fidelity-audit-suite that shipped 2026-05-16. Both are tracked in `doc/BACKLOG.md` under `[2026-05-16] Feature: fidelity-audit-suite (#27)`.

**Gap 1 — `cost-telemetry-zero`.** The autouse fixture in `tests/test_fidelity_audit.py:87-131` writes a per-test JSONL cost record to `tests/_artifacts/fidelity-audit-cost.jsonl`, but every live test stores `{input_tokens:0, output_tokens:0, cost_usd:0.0}`. AT11's field-presence check passes, but the artifact is semantically empty — defeating the cost-record's whole purpose for the fidelity moat. Wire `ResultMessage.usage.input_tokens`, `.usage.output_tokens`, and `.total_cost_usd` into `_test_cost_data` at each `ResultMessage` break-point in the 7 live test classes (`TestBundledPackDispatchFidelity`, `TestSkillDiscoveryFidelity`, both `TestHookFiringFidelity` classes, `TestPluginScopeFidelity`, `TestMcpResolutionFidelity`, `TestAgentFormatYamlPolymorphism`). Auth-failure class is excluded — no real SDK call. Validates under `CLAUDE_CREW_LIVE_TESTS=1` — real spend (~$0.35).

**Gap 2 — `yaml-loader-bypass`.** `TestAgentFormatYamlPolymorphism` manually parses the `.yaml` agent file via `yaml.safe_load` and constructs `AgentDefinition` inline; `build_merged_pack` is called for the markdown side only because `discover_dir` globs `*.md`. A regression breaking YAML support in the loader would not flip the test. Fix path (a): extend `claude_crew/subagents/_loader.py::discover_dir` to glob `*.yaml`/`*.yml` and route AT8 through `build_merged_pack` end-to-end. This closes the moat gap rather than amending the claim.

**Pointers for the planner:**
- `tests/test_fidelity_audit.py` (the 1091-LOC live-gated module shipped in #27)
- `claude_crew/subagents/_loader.py` (the `discover_dir` function and its callers)
- `claude_crew/sdk_teammate.py::_collect_response_text` (the canonical pattern for extracting `ResultMessage.usage` per the CLAUDE.md "SDK behavior" section)
- `doc/features/FEATURE-fidelity-audit-suite.md` (the per-feature record; this slice's known-gaps section is the origin doc)
- CLAUDE.md "Test conventions" — the four conventions landed in #27 (imports at top, `get_running_loop`, bounded async drains, `_preserve_sdk_auth`) apply here.

**Constraints:**
- Live-gate stays — both gaps validate under `CLAUDE_CREW_LIVE_TESTS=1`. Default-CI runs must still skip cleanly.
- Don't rewrite the autouse fixture's wire format unless the simpler `_test_cost_data` dict update path proves untenable. The task-0 debrief's "yield results" idea is a *better* shape but a bigger change.
- YAML loader extension must keep markdown discovery working (no regression on the canonical `.md` path).

**Out of scope:**
- Adding new fidelity claims. This slice closes two gaps in existing claims, doesn't grow the surface.
- Refactoring the fixture into per-test usage capture (a cleaner shape but breaks the "minimum diff" constraint).
- Touching the xfail-strict frontmatter test (`test_fidelity_audit_frontmatter.py`) — it's the Windows-CRLF documented gap, untouched.

Suggested validation: run `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_fidelity_audit.py tests/test_fidelity_audit_frontmatter.py -v` and assert (1) 10 pass + 1 xfail (matching #27 baseline) and (2) the resulting `tests/_artifacts/fidelity-audit-cost.jsonl` has at least 7 lines where `input_tokens > 0` AND `output_tokens > 0` AND `cost_usd > 0.0`. The YAML loader change is asserted by AT8 running end-to-end through `build_merged_pack` (no manual `yaml.safe_load` in the test body).

## Cycle

Cycle: 0
Prior review report (empty on cycle 0): 

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).

### Reference Artifacts

Spec template (read before writing the spec):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.2.9/doc/templates/spec-template.md`

Breakout template (reference only; produced by the breakout planner, not by you):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.2.9/doc/templates/breakout-template.md`

Existing specs in this worktree:
_None._

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups`

Change to this directory before all file operations.

## Instructions

- The spec must include `## Problem`, `## Acceptance Tests`, `## Test Command`, and `## Out of Scope`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Write the spec file only. Do not implement any code.
