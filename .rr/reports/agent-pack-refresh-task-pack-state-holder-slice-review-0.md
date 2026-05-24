# Slice Review: agent-pack-refresh task=pack-state-holder

## Summary

Task introduces `_PackState` holder in `claude_crew/factories.py` and refactors `default_factory`'s sdk branch so `factory()`, `_resolve_role`, and `_resolve_agent_def` read `pack`/`role_ss`/`bodies` LIVE off the holder rather than from captured closure-locals. `home_dir`/`project_root` are frozen on the holder for the future `refresh()`. A new `tests/test_pack_refresh.py` carries AT-3.

## Slice Adherence (AT-3)

- `_PackState` dataclass present with `pack`, `role_ss`, `bodies`, `home_dir`, `project_root`, `_lock` — matches spec contract <slice.spec.contract />.
- `factory()` (L288-290) reads `holder.pack/role_ss/bodies` at call-entry. `_resolve_role` (L260) reads `holder.pack`. `_resolve_agent_def` (L392) reads `holder.pack` live each call.
- `factory._holder` exposed (L407) so tests can mutate directly.
- AT-3 test (`test_resolver_returns_agent_def_after_direct_holder_mutation`) mutates `holder.pack` directly — no `refresh_pack()` involved, as the breakout requires <slice.adherence.met />.
- Three sub-tests (None-before, set-after, removal-after) collectively prove live-read indirection.
- No `refresh()` method, no `refresh_pack` attribute, no MCP-tool work leaked from tasks 2/3 <slice.scope.clean />.

## Non-Regression

Re-ran `uv run pytest`: **1203 passed, 32 skipped, 1 xfailed** in 91.74s. Matches implementor's claim (1200 baseline + 3 new AT-3 tests) <slice.regression.none />.

## Code-Quality Smoke

- `factories.py`: clean. `threading.Lock` default via `dataclasses.field(default_factory=...)`. Live-read pattern uniformly applied. Comments explain rationale at each read-site.
- `test_pack_refresh.py`: clear class/docstring tying to AT-3; explicit "no refresh_pack() involved" notes; three focused assertions; uses canonical `AgentDefinition` import.
- Minor (Info, non-blocking): `import dataclasses` appears both at module top (L10) and locally inside the sdk branch (L198) — the local one predates this slice; left alone is fine.
- Initial-load path semantics unchanged: `collect_startup_diagnostics`, direct-attach fallbacks, `_bmp_kwargs` gating, and `startup_diagnostics` freeze all preserved.

## Findings

None at Critical/High/Medium/Low.

**Info:**
- Pre-existing local `import dataclasses` inside `if mode == "sdk":` (L198) is now redundant with the module-top import added in this slice. Cleanup candidate for a later pass.

## Verdict

All three checks pass: slice adheres to AT-3, full suite green, no quality smells.
