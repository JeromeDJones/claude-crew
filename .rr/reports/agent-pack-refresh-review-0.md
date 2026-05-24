# Plan Review: agent-pack-refresh (cycle 0)

**Spec:** .rr/specs/agent-pack-refresh.md
**Cycle:** 0
**Reviewed:** 2026-05-24
**Verdict:** REQUEST-CHANGES

## Summary

Spec body is strong: clear problem, surgical architecture, full call-site survey, JSON-typed contract, decisions tagged to ATs, edge cases and sad paths well-covered, runnable test command. The `## Task Breakout`, however, misassigns two acceptance tests across the two tasks in a way that breaks the "one AT, one task, atomically validated by that task's test command" contract — AT-5 and AT-10 both depend on artifacts produced in task 2 but are claimed by task 1.

## Findings

### Critical

_None identified._

### High

- [HIGH-01] `spec.task-breakout.dependency-violation` — Task Breakout: AT-10 ("Stub-mode no-op refresh" — invokes the `refresh_agents` **MCP tool** via `make_server()` in stub mode) is assigned to `pack-state-holder-and-refactor` (task 1), but the `refresh_agents` MCP tool is only registered in task 2 (`mcp-refresh-agents-tool`). Task 1's declared `testCommand` (`pytest tests/test_factories.py tests/test_pack_refresh.py -x`) cannot satisfy AT-10 because the tool does not yet exist after task 1 completes. Implementor will either fail the gate or be forced to silently pull task-2 work forward. Category: clarify-assumption (re-assign AT-10 to task 2, or split: 10a holder-level no-op result + 10b MCP-tool integration).

- [HIGH-02] `spec.task-breakout.split-assertion` — Task Breakout: AT-5 asserts **two** things — (a) `RefreshResult["note"]` contains `"future-spawns-only"` AND (b) the `refresh_agents` MCP tool's docstring contains the same substring. (b) is created in task 2 (`server.py` tool registration), but AT-5 is assigned only to task 1. Task 1's test command cannot validate the docstring half. Either move AT-5 to task 2, split it into 5a (holder-level note) + 5b (tool docstring), or list it under both tasks' `acceptanceTests` with explicit scope on each side.

### Medium

- [MED-01] `spec.task-breakout.test-command-scope` — Task Breakout: Repo CLAUDE.md explicitly warns ("Validate the whole suite when changing widely-consumed behavior") that a feature gate scoped via hand-picked file lists has merged regressions before (the `multi-scope-agent-memory` precedent it cites). Both per-task `testCommand`s here are narrowly file-scoped (`test_factories.py test_pack_refresh.py` / `test_server.py test_pack_refresh.py`). The spec's own `## Test Command` correctly mandates `uv run pytest` (full suite); the breakout's per-task commands silently relax that. Either declare full-suite as the per-task gate or add an explicit final-task whole-suite gate. Category: align-with-vision.

- [MED-02] `spec.task-breakout.task1-size` — Task Breakout: `pack-state-holder-and-refactor` bundles (a) introducing `_PackState`, (b) refactoring three read-sites to read live, (c) implementing `refresh()` with diff + atomic swap + warning capture, (d) attaching `factory.refresh_pack`, AND (e) attaching `stub_factory.refresh_pack`. It owns 9 of 11 ATs. Not a true mega-task (single file, single concern: pack state lifecycle), but the asymmetry against task 2 (2 ATs) plus the AT misassignment above suggests a cleaner three-task split: (1) holder refactor + read-site indirection (ATs 3, 6), (2) refresh() method + diff + atomic swap + warning capture (ATs 1, 2, 4, 7, 8), (3) MCP tool wiring (ATs 5, 9, 10, 11). Optional restructure; flagging because it would dissolve HIGH-01/HIGH-02 cleanly.

### Low

- [LOW-01] `spec.acceptance-tests.at8-warning-shape` — Acceptance Tests: AT-8 asserts `warnings` contains a record "whose `message` references `bad.md`". The `StartupDiagCollector` produces `StartupDiagnostic` frozen dataclasses with category classification; the spec contract says `warnings: [{"level", "logger", "message"}]`. Implementor must serialise `StartupDiagnostic` into that 3-key dict — not stated explicitly. Add an Assumption or Decision pinning the serialisation shape. Category: clarify-assumption.

- [LOW-02] `spec.data-contracts.diff-naming-precedence` — Data/API Contracts: `diff.added` example shows `"role-a"` and `"plugin:role-b"` (plugin-namespaced); `diff.removed` shows `"role-c"` (bare). The role-key naming convention across the four layers (default/plugin/user/project) and which layer's namespacing wins is not specified. AT-7 says user-layer addition surfaces in `diff.added` but doesn't pin the key shape. Add one line stating role keys are whatever `merged_pack` already keys on. Category: clarify-assumption.

## Persistent Findings

_None — first cycle._

## Spec Quality

- **Acceptance tests:** Clear
- **Test command:** Present and runnable
- **Scope boundary:** Clear

## Verdict Rule Applied

REQUEST-CHANGES: 2 High findings (AT-10 mis-assigned across task dependency boundary; AT-5 has assertions split across two tasks).
