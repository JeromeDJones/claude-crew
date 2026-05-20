# Slice Review: multi-scope-agent-memory task=write-guard-noncollision-regression

## Scope

Task index 4, cycle 0. Acceptance test owned: **12**. Files touched: `tests/test_teammate_memory.py` only (matches spec: "No production code change — this task pins the documented non-collision invariant"). `implementationKind: behavior-change` declared but in practice this is a regression-pin slice with no production delta — correct per the breakout's stated charter.

## Check 1 — Slice adherence

- **AT-12 (project- and local-scope memory paths return `False` from `is_lead_project_memory_path`)**: covered by four assertions in the new `TestWriteGuardNoncollision` class:
  - `test_project_scope_memory_dir_not_flagged` — `<root>/.claude/agent-memory/<role>` ✅
  - `test_project_scope_memory_file_not_flagged` — `<...>/MEMORY.md` inside it ✅
  - `test_local_scope_memory_dir_not_flagged` — `<root>/.claude/agent-memory.local/<role>` ✅
  - `test_local_scope_memory_file_not_flagged` — `<...>/MEMORY.md` inside it ✅

Tests use `memory_dir()` itself to construct the candidate path rather than hardcoded path literals — this is the right pin, because if the path convention shifts the test follows the convention and only fails when the *guard* would actually let a wrong shape through. Both directory-form and file-form variants are exercised, which guards against any future positional check that might require trailing segments.

## Check 2 — Non-regression

- Slice command (`-k "lead_project_memory or noncollision"`): **6 passed** (4 new noncollision + 2 pre-existing `lead_project_memory` substring matches in `TestIsLeadProjectMemoryPath`).
- Full validation (`tests/test_teammate_memory.py tests/test_sdk_teammate.py`): **184 passed** — all upstream slices remain green (path-resolution, per-scope-guidance, write-tool, sdk-injection).

## Check 3 — Code-quality smoke

- Test class scoped to the new invariant; docstring names AT-12 and explains intent.
- Reuses the `tmp_path` fixture rather than `fake_home` — appropriate because `is_lead_project_memory_path` checks against `Path.home()/.claude/projects/`, not the project root; the project memory paths under `tmp_path` are unrelated to the protected zone regardless of HOME.
- No production change, so no risk of guard logic drift; the test correctly serves as a non-regression pin per spec design decision "Verify non-collision with `is_lead_project_memory_path` … No code change to the guard; a regression test asserts the non-collision".
- No inline imports, no fixture additions, idiomatic placement adjacent to the `TestIsLeadProjectMemoryPath` block.

No Critical / High findings.

## Verdict

AT-12 is covered by four explicit assertions spanning both new scopes and both directory/file forms; slice tests pass; full feature validation passes (184); no production code change as the spec specified for this regression-pin task.

**Verdict:** PASS
