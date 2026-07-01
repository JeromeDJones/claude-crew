# Build Report: m3-5-reshape-live-crew (cycle 0)

**Verdict:** PASS
**Cycle:** 0
**Generated:** 2026-06-30

## Tests Run

- **Declared command:** `uv run pytest tests/test_d0_send_to_unconditional.py tests/test_scoped_send.py`
- **Actual command:** `uv run pytest tests/test_d0_send_to_unconditional.py tests/test_scoped_send.py` (then broader `uv run pytest tests/test_sdk_teammate.py tests/test_scoped_send.py`)
- **Divergence reason:** None — also ran broader regression suite as required by task instructions.
- **Exit code:** 0
- **Passed:** 30 (slice) / 160 (broader regression) / **Failed:** 0 / **Total:** 30 / 160

## Failing Tests

_None._

## Uncovered / Partially Covered Tests

- AT 3: Covered by `TestWithOutEdgesSendToWired` — integration confirmed via captured ClaudeAgentOptions. The broker authorization path (`authorize_send`) itself is not re-tested here (it is covered by existing `test_scoped_send.py::TestScopedSendAuthorize` suite which passes unchanged).

## Files Changed

<!-- Output of: git diff --name-status HEAD (from worktree root)
     Injected verbatim by rr-implementor. Do NOT replace with prose narration. -->

```
M	claude_crew/sdk_teammate.py
M	tests/test_sdk_teammate.py
?	tests/test_d0_send_to_unconditional.py  (new untracked file)
```

## Implementation Summary

### `claude_crew/sdk_teammate.py` — behavior change

Removed the `_has_out_edges` conditional gate (~lines 1542–1560 before change). The 7-line block:
```python
_has_out_edges = any(n.get("direction") == "out" for n in (self._neighbors or []))
if _has_out_edges:
    ...
```
was replaced with an unconditional block (same body, comment updated to D0 rationale). The `__init__` comment on `self._neighbors` was also updated to remove the M2 "None/empty → tool not injected" claim.

Security is unchanged: `broker.authorize_send` (called inside `send_scoped`) is the enforcement boundary. The tool's mere presence does not grant any edge authorization.

### `tests/test_d0_send_to_unconditional.py` — new file

Covers AT 1, AT 2, AT 3:

- **`TestNoOutEdgesSendToWired`** (AT 1): 4 tests asserting a teammate spawned with `neighbors=None` or only `direction:"in"` entries has `_SEND_TO_MCP_SERVER_NAME` in `mcp_servers` and `_SEND_TO_TOOL_ID` in `allowed_tools`. Uses `ClaudeSDKClient` monkeypatch to capture `ClaudeAgentOptions` without live SDK.
- **`TestHasOutEdgesLiteralAbsent`** (AT 2): 1 test — reads `claude_crew/sdk_teammate.py` and asserts `"_has_out_edges"` is not present. Fails if the gate is re-introduced.
- **`TestWithOutEdgesSendToWired`** (AT 3): 3 tests asserting the same wiring holds for teammates with `direction:"out"` neighbors (non-regression).

### `tests/test_sdk_teammate.py` — updated 3 tests

`TestSdkTeammateMcpServersWiring` had 3 tests asserting the old conditional contract:
- `test_pack_no_mcp_servers_no_options_key`: asserted `opts.mcp_servers == {} or None` → updated to assert only `_SEND_TO_MCP_SERVER_NAME` is present (no extra pack-declared servers).
- `test_pack_inline_dict_reaches_options`: asserted `opts.mcp_servers == {"local-x": ...}` → updated to assert `local-x` and `_SEND_TO_MCP_SERVER_NAME` both present.
- `test_pack_string_name_resolves_via_patched_user_config`: asserted `opts.mcp_servers == {"atlassian": ...}` → updated to assert both `atlassian` and `_SEND_TO_MCP_SERVER_NAME` present.

## Scope-Creep Entries (this cycle)

_None._

## Blocker Reason

N/A — PASS verdict.
