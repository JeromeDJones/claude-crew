# Slice Review: agent-pack-refresh task=refresh-method-and-diff

## Summary

Task implements `_PackState.refresh()` with diagnostic capture, diff computation, atomic swap under lock, and rebuild-failure preservation. All six assigned ATs (1, 2, 4, 6, 7, 8) have direct test coverage in `tests/test_pack_refresh.py`. Full suite passes locally (1209 passed, 32 skipped, 1 xfailed — matches build report exactly).

## Check 1: Slice Adherence

- **AT-1** [`slice.adherence`] PASS `TestRefreshAddsNewProjectAgent` — writes `foo.md` post-startup, asserts `foo` in `diff.added` and resolver returns it.
- **AT-2** [`slice.adherence`] PASS `TestRefreshDiffClassification` — edits `a`, adds `b`, removes `c`; asserts correct buckets and no false positives. Diff uses `dataclasses.asdict` comparison per spec (factories.py:223).
- **AT-4** [`slice.adherence`] PASS `TestRefreshFailurePreservesPriorPack` — monkeypatches `build_merged_pack` to raise; asserts `ok=False`, `error` populated, prior role still resolves. Code (factories.py:178–245) confirms the rebuild executes inside `try/except` *before* the `with self._lock:` swap block; swap is gated by `assert new_pack is not None` after the error-path early-return. No torn pack possible.
- **AT-6** [`slice.adherence`] PASS `TestRefreshUsesCapturedRoots` — `_make_sdk_factory` passes `home`/`project_a` explicitly, test then `monkeypatch.chdir(project_b)`, asserts `alpha` (in A) resolvable and `beta` (in B/cwd) absent. Code calls `build_merged_pack(home_dir=self.home_dir, project_root=self.project_root)` — never reads cwd.
- **AT-7** [`slice.adherence`] PASS `TestRefreshPicksUpUserLayerChanges` — adds file under `home/.claude/agents/`, asserts `user-role` in `diff.added`. Proves user layer is re-merged.
- **AT-8** [`slice.adherence`] PASS `TestRefreshMalformedFileIsolated` — the malformed `bad.md` uses genuinely invalid YAML (unclosed list `tools: [Read` followed by `---`), not a file that happens to parse. Asserts `ok=True`, `warnings` references `bad.md`, `good-role` still resolves.

**Lock discipline** [`slice.adherence`] PASS Swap happens under `with self._lock:` (factories.py:242). Snapshot of `old_pack` outside lock is a reference grab (atomic in CPython); spec explicitly accepts last-writer-wins semantics for concurrent refreshes.

**Scope** [`slice.adherence`] PASS No MCP tool registration (deferred to task 3). `stub_factory.refresh_pack = _stub_refresh_pack` present with shape parity (zero counts, empty diff, future-spawns-only note).

## Check 2: Non-Regression

- `uv run pytest` -> **1209 passed, 32 skipped, 1 xfailed** (matches build report 1203->1209 delta = 6 new tests for AT-1/2/4/6/7/8).
- No prior tests broke; refactor's read-fields-live invariant from task 1 preserved.

## Check 3: Code-Quality Smoke

- `_PackState.refresh()` is ~100 lines, single responsibility, readable. Diagnostic capture mirrors `default_factory()` pattern (DRY trade-off acceptable — small duplication, identical fallback semantics).
- `try/except Exception` is appropriately broad per AT-4 contract (any rebuild error → `ok=False`).
- Diff helpers use `sorted()` for deterministic output — matches AT-2 "any deterministic order" allowance.
- `_REFRESH_NOTE` constant deduplicated across stub + sdk paths. Good.
- Per-layer count breakdown (default/user/project) returns 0 — flagged in build report as scope-creep, not asserted by any AT in this slice. [`slice.code-quality`] **Info**: AT-9/10/11 (task 3) inspect counts shape only, not per-layer values. Recovery would require re-loading each layer separately or threading per-layer counts back from `build_merged_pack`. Acceptable deferral — CARRIED TO FEATURE-REVIEW by coordinator for a feature-intent ruling (the idea promised "per-source counts").
- `factory._holder` exposure is a test seam — acceptable; already used by task 1 (AT-3).

[`slice.info`] Cross-slice: task 3 will need to confirm the MCP tool returns the dict shape verbatim and that the `note` substring `"future-spawns-only"` appears in both the tool docstring (AT-5) and `RefreshResult.note` (already present here).

## Findings

None at Critical or High severity.

## Verdict

PASS — all six ATs covered with appropriate tests, full suite green, atomic-swap and captured-roots invariants correctly implemented and proven by their tests, scope held to the slice.
