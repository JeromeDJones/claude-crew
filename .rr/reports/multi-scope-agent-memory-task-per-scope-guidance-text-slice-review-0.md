# Slice Review: multi-scope-agent-memory task=per-scope-guidance-text

## Scope

Task index 1, cycle 0. Acceptance tests owned: **5, 6, 7**. Files touched: `claude_crew/teammate_memory.py`, `tests/test_teammate_memory.py`.

## Check 1 — Slice adherence

- **AT-5 (user scope)**: `build_memory_section(..., scope="user")` text contains "apply across projects" (in both `_INSTRUCTIONS_HEADER_USER` and `_WHAT_TO_SAVE_USER`) and the test asserts neither "project-scoped memory" / "local-scoped memory" / ".gitignore" appear. Covered by `test_user_scope_contains_cross_project_phrase`, `test_user_scope_no_project_specific_phrasing`. ✅
- **AT-6 (project scope)**: project header names "project-scoped memory", "committed alongside the code", "shared across the whole team"; `_WHAT_NOT_TO_SAVE_PROJECT` warns about secrets and machine-specific detail; path renders `<root>/.claude/agent-memory/<role>`. Covered by `test_project_scope_emphasizes_committed_shared_memory`, `test_project_scope_warns_against_secrets`, `test_project_scope_names_project_path`. ✅
- **AT-7 (local scope)**: local header describes "machine-local", "not committed", mentions experimental notes; recommends `.gitignore` entry `.claude/agent-memory.local/`; path renders `<root>/.claude/agent-memory.local/<role>`. Covered by `test_local_scope_describes_machine_local_memory`, `test_local_scope_mentions_experimental_notes`, `test_local_scope_recommends_gitignore_entry`, `test_local_scope_names_local_path`. ✅

Signature shape (`scope`, `project_root` kwargs added to `build_memory_section`) matches the spec contract. Default `scope="user"` preserves the one-positional-arg form — confirmed by the unchanged `TestBuildMemorySectionNoIndex` block still passing. Three sibling constant triples per scope, picked in a tight if/elif/else inside `build_memory_section` — matches the design decision "three full named blocks (header / what-to-save / what-not-to-save)".

## Check 2 — Non-regression

- Slice command (`-k "guidance or scope"`): **17 passed**.
- Prior task command (`-k "memory_dir or scope"`): **17 passed** — path-resolution slice unaffected.
- Full module (`tests/test_teammate_memory.py`): **59 passed** — boundary, write-guard, IO-error, no-spontaneous-mutation, persistence-note variants all still green.

## Check 3 — Code-quality smoke

- Three sibling block triples constructed at module level; no conditionals inside the templates — easy to audit, matches spec rationale.
- Boundary block (`Boundaries.` + `CLAUDE_CODE_DISABLE_AUTO_MEMORY` + write guard) preserved in all three headers — aligns with the spec assumption "boundary text stays in all three scopes".
- The else-branch defaults to user-scope guidance for unknown scope strings; runtime input is guarded by `memory_dir` raising on unknown scope before reaching here, so dead path. **Info**: same observation as the prior slice — tighten `scope: str` → `Literal` once the SDK injection task lands.
- One minor: project header says memory lives "inside the project repository at `.claude/agent-memory/`" — relative path, but `{directory}` interpolation supplies the absolute path elsewhere. Acceptable; matches the spec's "names the project-scoped path" requirement via the absolute path placeholder used downstream.
- `tmp_path` is sometimes passed alongside `fake_home` redundantly. Harmless. **Info** only.

No Critical / High findings.

## Verdict

All three owned acceptance tests pass; slice and cross-slice non-regression both green; code is clean. No Critical/High findings.

**Verdict:** PASS
