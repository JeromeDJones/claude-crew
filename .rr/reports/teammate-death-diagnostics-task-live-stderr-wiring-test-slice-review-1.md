# Slice Review: teammate-death-diagnostics task=live-stderr-wiring-test (cycle 1)

**Verdict: PASS**
**Cycle:** 1

## Summary

Re-review after rework. Cycle-0 was REQUEST-CHANGES on HIGH-01 (test blind to registration line 1432 — would pass even with the SDK callback registration deleted) and MED-01 (docstring falsely claimed method accessibility proved registration). Both are resolved. I verified the fix myself with a live mutation test: disabling `claude_crew/sdk_teammate.py:1432` turns the gated test **RED**, restoring it turns it **GREEN**. The test is now genuinely regression-sensitive to the wiring AT-8 exists to guard.

## Resolution of prior findings

### HIGH-01 (`slice.adherence.false-pass`) — RESOLVED

The rework adds a registration assertion that introspects the live SDK client: `registered = client.options.stderr`, then asserts `registered is not None`, `registered.__self__ is tm`, and `registered.__func__ is SdkTeammate._on_stderr_line`. The `__self__`/`__func__` decomposition is the correct way to compare — bound methods are freshly created per attribute access, so a naive `is` check would spuriously fail. The direct `_on_stderr_line(PROBE)` call is retained but now explicitly scoped as a separate ring-to-snapshot smoke, not the registration proof.

**Decisive mutation verification (reviewer-run):**
- Located the importable module — resolves to this worktree's `claude_crew/sdk_teammate.py`.
- Commented out line 1432 → gated test **FAILED, exit 1**, on `AssertionError: client.options.stderr is None — ...sdk_teammate.py:1432 was not executed (or was deleted).`
- Restored line 1432 via `git checkout` → re-ran → **1 passed, exit 0**.

The test now fails when the wiring is broken and passes when it's intact. AT-8's "registered ... end-to-end" intent is genuinely met.

### MED-01 (`slice.quality.style`, misleading docstring) — RESOLVED

The module and class docstrings are rewritten. They no longer claim accessibility proves registration; they correctly split the two steps — registration proof via `client.options.stderr` introspection (sensitive to line 1432), and ring-to-snapshot smoke (explicitly labeled "not a substitute for the registration proof"). The inline comment explaining the `__self__`/`__func__` comparison is accurate.

## Scope (taskTouches — Invariant 1)

`slice-touches-check.sh` → **EXIT=0**. The single untracked file `tests/test_live_stderr.py` matches the declared glob. The `sdk_teammate.py` mutation was the reviewer's, fully reverted (`git diff` clean).

## Non-regression

- `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py` → **1 passed, exit 0** (real SDK subprocess, line 1432 restored).
- `uv run pytest tests/test_sdk_teammate.py tests/test_broker.py` → **225 passed, exit 0**.
- Ungated skip behavior preserved.

## Code-quality smoke

Clean: gating, bounded 90s drain, `get_running_loop()`, auth-preservation helper, no secrets, no swallowed exceptions.

### Critical / High / Medium
_None identified._

### Low
- [LOW-01] `slice.quality.style` — inline `from claude_crew.sdk_teammate import SdkTeammate` should be hoisted to module top per CLAUDE.md. Cosmetic. (Coordinator note: fixed at merge — import hoisted to the module-top block.)

### Info
_None identified._

## Findings Disposition

| finding-id | severity | tag | disposition | rationale |
|------------|----------|-----|-------------|-----------|
| HIGH-01 | High | slice.adherence.false-pass | addressed | Registration assertion added; reviewer-run mutation test confirms RED on delete-line-1432, GREEN on restore. |
| MED-01 | Medium | slice.quality.style | addressed | Docstrings rewritten to distinguish registration proof from ring smoke. |
| LOW-01 | Low | slice.quality.style | addressed-at-merge | Inline import hoisted to module top by coordinator before merge. |
