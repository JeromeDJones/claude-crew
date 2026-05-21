# Slice Review: click-to-view-tool-output task=ui-route

## Inputs verified

- Owned ATs: 5 (live hit), 6 (404/400), 9 (tool_use_id on merged record).
- Changed files: `claude_crew/ui_server.py` (+47), new `tests/test_ui_server_tool_output.py`.

## Check 1 — Slice adherence

- **AT-5**: `_handle_tool_output` delegates to `Broker.get_tool_output`; on hit returns `{"body", "truncated", "redaction_version": "v1"}` with 200. Body comes straight from the redacted store — route does NOT re-fetch raw text or bypass the store.
- **AT-6**: Unknown teammate_id → `Broker.get_tool_output` returns `None` → 404 `{"error":"not_found"}`. Bad param `bad..id` (contains `.`) fails `^[A-Za-z0-9_\-]+$` → 400 `{"error":"invalid_param","param":"teammate_id"}`. Both `teammate_id` and `tool_use_id` are validated.
- **AT-9**: `_build_state` tool-event merge includes `"tool_use_id": ev.tool_use_id` on the kind:"tool" record.

Security posture:
- Path-param regex blocks `.`, `/`, NUL, spaces, `..` traversal — matches spec edge case.
- Route registered in `_make_app` only; `serve()` unchanged → localhost bind inherited. No host override.
- Structured 500 via `try/except Exception` with `_logger.exception`; mirrors `/wait-messages` pattern.

## Check 2 — Non-regression

```
tests/test_ui_server_tool_output.py + test_tool_outputs.py + test_ui_server.py
→ 146 passed in 3.42s
```

`_build_state` additive change (only adds `tool_use_id` key) does not break existing `test_ui_server.py` assertions on the merged-messages payload.

## Check 3 — Code-quality smoke

- **Info** [slice.contract]: `truncated = len(body.encode("utf-8")) >= 4096` infers truncation from stored size rather than a flag from the store. With the belt-and-suspenders cap that appends `…` when over the limit, capped bodies land at exactly 4096 bytes so this works; a natural body of exactly 4096 bytes would be a false-positive `truncated=true`. Edge case, not a verdict blocker. Future: have `Teammate.get_tool_output` return a `(body, truncated)` tuple or stash a flag at store time.
- **Info** [slice.style]: `_PATH_PARAM_RE` is module-level — appropriate (compiled once, reused).
- Handler returns `JSONResponse` consistently for all paths (200/400/404/500). Good.
- Error responses are structured JSON, never bare stack pages.

No High/Critical findings.

## Verdict

PASS — slice adherent (AT-5/6/9), non-regressive (146 green), security posture sound.
