# Feature Review: multi-scope-agent-memory

## Inputs surveyed

- Spec: `.rr/specs/multi-scope-agent-memory.md` (12 ATs, 5 tasks, 5 design decisions).
- 5 slice-review reports (all PASS, cycle 0).
- Source: `claude_crew/teammate_memory.py`, `claude_crew/sdk_teammate.py:480-545`.
- Validation command run: `uv run pytest tests/test_teammate_memory.py tests/test_sdk_teammate.py` → **184 passed in 4.17s**.
- No architecture doc in repo (noted; not blocking).

## Check 1 — Cross-slice integration coherence

The five slices compose into a single seam at `SdkTeammate.__init__:497-544`:

1. `path-resolution-multi-scope` provides `memory_dir`/`memory_index_path` with `scope` + `project_root`.
2. `per-scope-guidance-text` adds three template families consumed by `build_memory_section`.
3. `ensure-write-tool-helper` provides a non-mutating Write-attach.
4. `sdk-teammate-multi-scope-injection` wires (1)+(2)+(3) at construction: resolves `project_root = Path(cwd).resolve() if cwd else Path.cwd()` for project/local; calls `ensure_write_tool` *unconditionally* inside the scope guard (so Write is attached even when a `system_prompt` override suppresses prompt-side memory I/O — correct decoupling); replaces `self._agents` with a fresh dict (F3b shared-pack invariant preserved); rebinds `role_def = patched_def` so the subsequent `getattr(role_def, "tools", None)` sees Write.
5. `write-guard-noncollision-regression` pins AT-12.

Path shapes used by the SDK call (`Path(cwd).resolve() / ".claude" / "agent-memory{,.local}" / <role>`) match the shapes the regression test pins against `is_lead_project_memory_path`. No skew. `tag: feature.integration.coherence` — coherent.

## Check 2 — Holistic spec satisfaction

- AT 1–12: each is covered by a directly-named test in the slice reports; full validation run passes 184 tests including all keyword-filtered subsets.
- Design decisions in spec → implementation:
  - Keyword `scope` defaulting to `"user"` ✅ (back-compat for `memory_dir("role")`).
  - Path layouts `<root>/.claude/agent-memory/` and `<root>/.claude/agent-memory.local/` ✅.
  - Project root from `self._cwd`, fallback to `Path.cwd()` ✅.
  - Three named templates, not conditionals in one block ✅.
  - Write auto-attach lives in `sdk_teammate.__init__` via `dataclasses.replace`, fresh `self._agents` dict ✅.
  - Local-scope guidance recommends `.gitignore` entry, does NOT auto-edit ✅ (string contains "Recommended `.gitignore` entry").
  - Non-collision verified, no guard code change ✅.
- Out-of-scope items respected (no guard logic change, no `.gitignore` mutation, no migration tooling).

`tag: feature.spec.satisfied` — all 12 ATs and 5 design decisions delivered.

## Check 3 — Cracks-fell-through

Two recurring Info threads from slice-review:

**Thread A — `scope: str` vs `Scope = Literal["user","project","local"]`** (spec Data/API Contracts line 47). Flagged by both `path-resolution` and `per-scope-guidance` slice reviewers as Info-tier with "tighten later" recommendation. Holistic call: the contract symbol `Scope` is declared in the spec but never defined in code — there is no module-level `Scope` alias and no Literal anywhere in the signatures (`memory_dir`, `memory_index_path`, `build_memory_section`). The runtime `else: raise ValueError(f"Unknown scope: {scope!r}")` in `memory_dir` provides a safety net, and `build_memory_section`'s `else` branch falls through to user-scope on unknown values (a soft default rather than a raise). This is a deviation from the spec's typed surface but not a behavioral defect — call sites in `sdk_teammate.py` constrain `role_memory` to the three literals before calling. **Severity: Info.** Recommend a follow-up to add the `Scope` alias and re-type the three signatures.

**Thread B — Inline imports** (flagged in `sdk-teammate-multi-scope-injection` and `ensure-write-tool-helper` slice reviews). Three locations:
- `sdk_teammate.py:507` — `from claude_crew.teammate_memory import build_memory_section, ensure_write_tool` inside `__init__`. Plausibly circular-import-guarded (teammate_memory imports `SENTINEL_MEMORY` from teammate_prompt; sdk_teammate likely imports teammate_prompt at module top), but no comment justifies it. Per project CLAUDE.md, inline imports inside test functions are a code smell; the same rule arguably applies to method bodies.
- Test files: inline imports of `AgentDefinition` and `SENTINEL_MEMORY` inside test method bodies.
Holistic call: the sdk_teammate inline import is the only one inside production code. It costs a tiny per-call import (cached in `sys.modules` after first hit) and obscures dependency graph. **Severity: Info.** Recommend hoisting after verifying no circular import.

**Other cracks checked:**
- `build_memory_section`'s `else` branch silently defaulting to user-scope guidance on an unknown scope is inconsistent with `memory_dir`'s `else: raise`. Currently dead because callers go through `memory_dir` first, but a future direct caller could get surprising fall-through. Info-tier only — not exposed by current code paths. `tag: feature.cracks.scope-fallthrough`.
- `Path(cwd).resolve()` is called eagerly only inside the scope guard; if `cwd` is a `PathLike` rather than `str`, `Path(cwd)` still works. No edge case missed.
- No tests stress the `cwd=None` fallback through `SdkTeammate.__init__` (AT-10/11 both pass `cwd=str(tmp_path)`). The fallback is exercised at the helper layer but not at the integration layer. **Info.** Not blocking — spec edge case names DEBUG-log behavior, the fallback is shallow.
- `ensure_write_tool` returns `tools=existing + ["Write"]` (a list) while the spec sketch says `tuple(tools)+("Write",)`. Slice review noted and accepted (SDK boundary normalizes). Confirmed: no other code path requires tuple identity.

No Critical or High findings.

## Verdict

All slices PASS; validation suite green (184/184); spec contract delivered end-to-end with the F3b shared-pack invariant preserved and the lead write-guard non-collision pinned by regression test. The two recurring Info threads (`scope: str` vs `Literal`, and inline imports) are surface-level type/style debts, not behavioral defects, and worth a small follow-up cleanup task — but they do not gate this feature.

Findings:
- **Info** — `feature.spec.type-drift.scope-literal`: declare `Scope = Literal["user","project","local"]` in `teammate_memory.py` and use it on `memory_dir`, `memory_index_path`, `build_memory_section`, plus `role_memory` in `sdk_teammate.py`. Tighten `build_memory_section` to raise on unknown scope (mirror `memory_dir`).
- **Info** — `feature.cracks.inline-imports`: hoist `from claude_crew.teammate_memory import build_memory_section, ensure_write_tool` to the top of `sdk_teammate.py` (verify no circular import first). Apply the same to test-file inline imports of `AgentDefinition` and `SENTINEL_MEMORY` per CLAUDE.md convention.
- **Info** — `feature.cracks.scope-fallthrough`: `build_memory_section` silently defaults to user-scope on unknown scope. Currently dead, but tighten the else to raise for consistency with `memory_dir`.
- **Info** — `feature.test.coverage-gap.cwd-none-integration`: no `SdkTeammate.__init__` test exercises the `cwd=None` fallback path for project/local scope. Helper-level coverage exists; integration-level coverage does not.

**Verdict:** PASS
