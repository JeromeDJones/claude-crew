# Build Report — scoped-send-teammate (cycle 1)

**Feature:** m2-edge-routing  
**Task:** scoped-send-teammate (index 2)  
**Cycle:** 1 (REWORK)  
**Verdict:** PASS  
**Date:** 2026-06-13

---

## Test Command

```
uv run pytest tests/test_scoped_send.py
```

## Result

**22 passed, 0 failed, 0 errors** — all acceptance tests green (17 from cycle 0 + 5 new).

Full suite (excluding live SDK + pre-existing flaky `test_shutdown_signals`): **1518 passed, 21 skipped, 1 xfailed**, no regressions.

---

## Rework Summary (what cycle 0 was missing)

The slice reviewer correctly identified that cycle 0 injected a `send_to` system-prompt advertisement but never registered the actual in-process MCP tool — leaving teammates with a tool name that resolved to nothing. Cycle 1 closes that gap.

---

## Acceptance Tests Covered

| AT | Description | Status |
|----|-------------|--------|
| AT#8 | Neighbor adjacency injected at spawn (prompt injection via `system_prompt_override`) | ✅ PASS (retained) |
| AT#9 | `broker.send_scoped` authorizes a declared neighbor, rejects a non-neighbor | ✅ PASS (retained) |
| AT#10 | Direct ping-pong a→b→a→b stays off the lead inbox (integration) | ✅ PASS (retained) |
| AT#11 | `send_to` MCP tool registered in SdkTeammate; handler delegates to `broker.send_scoped` | ✅ PASS (new) |

---

## Cycle 1 Implementation

### `claude_crew/sdk_teammate.py`

**New imports:**
- Added `create_sdk_mcp_server` to the `claude_agent_sdk` import line
- Added `from claude_agent_sdk import tool as sdk_tool`
- Added `UnauthorizedEdgeError` to the `claude_crew.broker` import

**New module-level constants:**
```python
_SEND_TO_MCP_SERVER_NAME: str = "crew-send"
_SEND_TO_TOOL_ID: str = f"mcp__{_SEND_TO_MCP_SERVER_NAME}__send_to"
```

**`__init__` additions:**
- `self._neighbors: list[dict] | None = neighbors if neighbors else None` — stored for `_run()` to gate tool injection
- `self._send_to_tool: Any = None` — set as side-effect by `_build_send_to_mcp_server()`; exposed for test introspection

**New method `_build_send_to_mcp_server()`:**
- Uses `@sdk_tool("send_to", description, {"recipient": str, "payload": dict})` to create an `SdkMcpTool`
- Handler calls `self._broker.send_scoped(self.id, args["recipient"], args.get("payload") or {})`
- `UnauthorizedEdgeError` → `{"content": [...], "is_error": True}` (broker-not-available and general exceptions handled similarly)
- Returns `create_sdk_mcp_server(_SEND_TO_MCP_SERVER_NAME, tools=[send_to_impl])`
- Sets `self._send_to_tool = send_to_impl` for test introspection

**Injection in `_run()`** (after `opts_kwargs["mcp_servers"] = {**pack_mcp_resolved, **spawn_mcp_resolved}`):
- Gated on `_has_out_edges` (any neighbor with `direction == "out"`) — preserves existing `mcp_servers` / `allowed_tools` contract for non-topology spawns
- Adds `crew-send` server to `opts_kwargs["mcp_servers"]`
- Appends `_SEND_TO_TOOL_ID` to `allowed_tools` (deduped)
- Appends `_SEND_TO_TOOL_ID` to `tools` if already restricted (deduped)

### `tests/test_scoped_send.py`

Added **`TestSendToToolRegistration`** class (5 tests):

| Test | What it verifies |
|------|-----------------|
| `test_send_to_mcp_server_shape` | `_build_send_to_mcp_server()` returns `McpSdkServerConfig` with `type="sdk"`, correct name; `_send_to_tool` side-effect set with `name=="send_to"` |
| `test_send_to_handler_delegates_to_send_scoped` | Handler calls `broker.send_scoped(self.id, recipient, payload)` and returns success |
| `test_send_to_handler_surfaces_unauthorized_as_error` | `UnauthorizedEdgeError` → `is_error=True` with "send_to rejected" text |
| `test_send_to_handler_when_broker_is_none` | `_broker=None` → `is_error=True` with "broker not available" text |
| `test_send_to_tool_id_constant` | `_SEND_TO_TOOL_ID == f"mcp__{_SEND_TO_MCP_SERVER_NAME}__send_to"` naming convention |

All tests use `SdkTeammate.__new__` to bypass `__init__` (no pack loading, no live SDK).

---

## Regression Fix (cycle 1 introduced, self-corrected)

The initial injection was unconditional — it fired for every `SdkTeammate` regardless of topology. This broke 14 existing tests in `test_e2e_pack_tool_allowlist.py` and `test_sdk_teammate.py` that assert exact `mcp_servers == {}` / `allowed_tools` content for non-topology spawns.

Fix: gated injection on `_has_out_edges` derived from `self._neighbors`. Teammates spawned without topology (no `neighbors=` param) do not get the `crew-send` server; the `send_to` tool only appears on the model's surface when the teammate actually has declared out-edges to message.

---

## Files Changed

```
M	claude_crew/sdk_teammate.py
M	tests/test_scoped_send.py
```

(All cycle 0 changes to `broker.py`, `factories.py`, `server.py`, `teammate_prompt.py` retained unchanged.)

---

## Pre-Existing Failures (not introduced by this task)

- `tests/test_shutdown_signals.py::TestSignalShutdown::test_sigterm_triggers_clean_exit_and_deregister` — fails with "claude-crew did not register within 15.0s". Confirmed pre-existing. Infrastructure/timing issue unrelated to this slice.
