# Build Report: reshape-docs-sync (cycle 0)

**Slug**: m3-5-reshape-live-crew  
**Task**: reshape-docs-sync (index 5)  
**Cycle**: 0  
**Verdict**: PASS  
**Date**: 2026-06-30  

---

## Test Command

```
uv run pytest tests/test_reshape_docs_staleness.py
```

## Result

```
============================= test session starts ==============================
collected 2 items

tests/test_reshape_docs_staleness.py::test_has_out_edges_not_in_architecture_md PASSED [ 50%]
tests/test_reshape_docs_staleness.py::test_reshape_crew_documented_in_architecture_md PASSED [100%]

============================== 2 passed in 0.02s ===============================
```

## Files Changed (`git diff --name-status HEAD`)

```
M	CLAUDE.md
M	doc/ARCHITECTURE.md
```

(Plus new file `tests/test_reshape_docs_staleness.py` — not tracked yet.)

---

## What Was Done

### AT 20 — Doc staleness guard (spec requirement)

Authored `tests/test_reshape_docs_staleness.py` with two grep-based assertions:

1. **`_has_out_edges` absent from `doc/ARCHITECTURE.md`** — NAMED LITERAL guard; passes (the string was never in the doc; confirms no regression).
2. **`reshape_crew` present in `doc/ARCHITECTURE.md`** — NAMED LITERAL presence guard; passes after doc update.

### `doc/ARCHITECTURE.md` updates

1. **Section heading** updated: `(M0 + M1.5 + M2 + M3)` → `(M0 + M1.5 + M2 + M3 + M3.5)`.
2. **Tool count** updated: `**17** MCP tools` → `**18** MCP tools` in the `server.py` section.
3. **`reshape_crew` added to the tool table** (after `adapt_shape`) with a full description: live crew requirement, five-verb dispatch table, M1.5 gate reuse, per-verb live effects (set_gate rewires overrides; add_node/augment spawn+inform no-respawn; drop records minus-topology + graceful kill + stale override cleanup + inform; swap spawn+record THEN kill), decline/timeout leaves crew untouched.
4. **`sdk_teammate.py` description** updated to note D0: "wired **unconditionally for every `SdkTeammate`** since D0"; explains that the security boundary is `broker.authorize_send` at delivery, not tool presence; notes this enables respawn-free live edge additions.
5. **M3 description** updated: removed "reshaping a running crew is the separately-milestoned M3.5" — replaced with accurate present-tense note that `adapt_shape` is pre-instantiation only (not all adaptation).
6. **M3.5 entry added** after M3: documents D0, unconditional `send_to` wiring, three additive broker helpers (`latest_topology`, `set_edge_override`, `remove_edge_overrides`), M1.5 gate reuse, tool count 17→18.
7. **"Scoped `send_to`" section** (Edge Routing M2): stale "spawned with a non-empty `neighbors` list" qualifier replaced with "unconditionally since D0 (M3.5, 2026-06-30)"; added security-boundary clarification: "`authorize_send` at delivery time, not tool presence."

### `CLAUDE.md` updates

1. **`server.py` description** updated: tool count 14→18; all 18 tools listed; `reshape_crew` described as applying one verb to an already-running live crew; stale `propose_shape` description (blocking only) corrected to mention non-blocking default.
2. **`sdk_teammate.py` description** updated to note D0: "send_to MCP server is wired unconditionally for every `SdkTeammate` at spawn — security boundary is `broker.authorize_send` at delivery; enables respawn-free live edge additions via `reshape_crew`."

---

## Scope Check

Changes are exactly scoped to `doc/ARCHITECTURE.md`, `CLAUDE.md`, and `tests/test_reshape_docs_staleness.py` as specified.  
No `git commit`, `git push`, or `git stage` was run.
