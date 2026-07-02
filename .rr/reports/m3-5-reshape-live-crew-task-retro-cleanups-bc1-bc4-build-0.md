# Build Report — retro-cleanups-bc1-bc4 — cycle 0

## git diff --name-status HEAD

```
M	doc/ideas/m3.5-reshape-live-crew.md
M	tests/test_reshape_crew_verbs.py
M	tests/test_reshape_docs_staleness.py
M	tests/test_sdk_teammate.py
```

## Per-BC summary

- **BC-1** — Hoisted `_SEND_TO_MCP_SERVER_NAME` into the module-top `from claude_crew.sdk_teammate import (...)` block in `tests/test_sdk_teammate.py`; deleted three inline `from claude_crew.sdk_teammate import _SEND_TO_MCP_SERVER_NAME` lines at former positions ~3103, ~3150, ~3189.
- **BC-2** — Added `broker._edge_overrides[("b", "c")] = "gated"` before the drop in `test_drop_removes_node_and_stale_override_and_informs`, plus assertions `("b", "c") not in broker._edge_overrides` and `["b", "c"] in acts["edge_overrides_removed"]`, covering the `_pair[0] == drop_slot` branch of the D6 sweep in `server.py:1582`.
- **BC-3** — Added `test_has_out_edges_not_in_claude_md` to `tests/test_reshape_docs_staleness.py`; mirrors the existing ARCHITECTURE.md test, asserts `_has_out_edges` absent from repo-root `CLAUDE.md`; passes (CLAUDE.md is already clean).
- **BC-4** — Inserted HTML comment `<!-- NOTE: the _has_out_edges gate was REMOVED by D0 in this feature; references below describe the pre-D0 behavior for historical context. -->` immediately before the D3 row in `doc/ideas/m3.5-reshape-live-crew.md`; historical content preserved.

## Test results

```
tests/test_sdk_teammate.py          138 passed
tests/test_reshape_crew_verbs.py      7 passed
tests/test_reshape_docs_staleness.py  3 passed
─────────────────────────────────────────────
Total                               148 passed in 3.82s
```

All three affected test files green in a single combined run. No cross-interactions observed.
