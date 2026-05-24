# Slice Review: agent-pack-refresh task=mcp-refresh-agents-tool

**Verdict:** PASS
**Cycle:** 0
**Scope checked:** `claude_crew/server.py`, `tests/test_pack_refresh.py`

## Check 1 — Slice Adherence (AT-5/9/10/11)

- **AT-5 (future-spawns-only in docstring + note):** PASS.
  - `server.py` `refresh_agents` docstring contains the phrase explicitly (block beginning "future-spawns-only: already-running teammates keep...").
  - Test `test_docstring_contains_future_spawns_only` reads `tool.fn.__doc__` and asserts the substring.
  - Test `test_refresh_result_note_contains_future_spawns_only` round-trips via MCP client and asserts `result["note"]` contains the phrase. Fallback branch references `_REFRESH_NOTE` from `factories.py` for the no-op case.
  - <slice.adherence>covered</slice.adherence>

- **AT-9 (sdk-mode RefreshResult shape, ok=True):** PASS.
  - `test_sdk_mode_refresh_returns_refresh_result_shape` builds an sdk-mode factory, calls `refresh_agents` over the MCP session, and asserts `ok=True` plus presence of `diff.{added,removed,changed}`, `counts.total`, `warnings`, `note`. Genuine end-to-end exercise (MCP client + sdk factory path).
  - <slice.adherence>covered</slice.adherence>

- **AT-10 (stub-mode: ok=True, empty diff, total 0, note present, no raise):** PASS.
  - `test_stub_mode_refresh_returns_ok_empty_diff` asserts `ok=True`, `diff == {added:[],removed:[],changed:[]}`, `warnings == []`, `counts.total == 0`. The default `make_server()` (stub mode) routes through `factory.refresh_pack` (or fallback no-op); either path satisfies the assertions.
  - <slice.adherence>covered</slice.adherence>

- **AT-11 (malformed file → ok=True, warnings reference it, server survives):** PASS.
  - `test_sdk_mode_malformed_file_ok_and_server_alive` plants `project/.claude/agents/bad.md` with broken YAML, calls `refresh_agents`, asserts `ok=True`, asserts a warning message mentions `bad.md`, then issues a follow-up `list_crew` over the SAME MCP session and asserts the response — directly proving the server didn't crash.
  - <slice.adherence>covered</slice.adherence>

## Check 2 — Non-Regression

- Coordinator-confirmed: `uv run pytest` → 1214 passed / 32 skipped / 1 xfailed, +5 over baseline 1209. No re-run required.
- <slice.nonregression>clean</slice.nonregression>

## Check 3 — Code-Quality Smoke

- `server.py` tool function:
  - Mirrors surrounding `@mcp.tool()` conventions (async, type hints, docstring, dict[str, Any] return).
  - `getattr(factory, "refresh_pack", None)` + explicit fallback dict is defensive and well-commented. No tight coupling to factory internals.
  - Inline `from claude_crew.factories import _REFRESH_NOTE` inside the fallback branch is a minor smell (module-private name reach + non-top-level import). Acceptable here because (a) it is the exact same sentinel string the live path returns, keeping the contract identical, and (b) the fallback is documented as "should not happen in practice." <slice.quality severity="info">Could be hoisted to a public constant or a top-level import on a future pass; not blocking.</slice.quality>
- `tests/test_pack_refresh.py`:
  - New imports (`json`, `asyncio`, `create_connected_server_and_client_session`, `make_server`) are placed mid-file rather than the module's top import block. Per project CLAUDE.md ("Imports at module top. Inline imports inside test functions are a code smell — put new imports in the module's existing import block"), these are below-section-header but not technically inside a function — borderline. Recommend hoisting on a follow-up pass. <slice.quality severity="info">Style nit; not blocking under the three-check charter.</slice.quality>
  - `asyncio` import is unused at the new section level — harmless.
  - Tests exercise behavior end-to-end through the MCP client transport (not just the function reference), giving real integration coverage.
  - `_content_json` helper handles both `structuredContent` and JSON-text fallbacks — sensible.

## Carried Info (acknowledged, not blocking)

`RefreshResult.counts` per-layer (default/user/project) is hardcoded to 0; only `total` and `plugin` populate. Build report documents this honestly; AT-9/10/11 assert shape only. Per task brief, this is a feature-review matter, NOT a slice REQUEST-CHANGES.

## Findings Summary

- Critical: none
- High: none
- Medium: none
- Low/info: two style nits (private-name import in fallback; new test imports mid-file). Non-blocking.

## Verdict Rationale

All four owned ATs are genuinely exercised by the new tests through the live MCP session; non-regression confirmed; code follows established patterns. No Critical/High findings.

RR-VERDICT: PASS agent-pack-refresh 0 /home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-task-mcp-refresh-agents-tool-slice-review-0.md
