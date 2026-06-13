# Slice Review: m2-edge-routing task=dashboard-edge-observability

**Verdict:** PASS
**Cycle:** 0 (reviewing cycle-1 build artifact — overlay-added rework)
**Task index:** 3 — owns AT#11, AT#12, AT#13
**Reviewer:** fresh slice-reviewer (prior retired at context cap); read all inputs from disk.

---

## Check 1 — Slice adherence (ATs 11–13 + deferred-test deliverable)

Both halves of the task are present and sound.

### Half A — green-suite data + endpoint contract (`claude_crew/ui_server.py`)

- **AT#11 (data contract):** `/api/state` local instance payload carries `topology_edge_stats`,
  each entry `{from_slot, to_slot, mode, exchanges, tripped, crew_id}` sourced from
  `snapshot.topology_edge_stats` with `crew_id = snapshot.crew_id` (ui_server.py:454+).
  Tests `TestEdgeStatsOnApiState` (4 tests) cover present-after-tee, empty-when-no-topology,
  crew_id-matches-broker, tripped-false. ✓
- **AT#12 (endpoint contract):** `GET /edge-log/{crew_id}/{from}/{to}` →
  `{ok, edge:[f,t], messages}`; `POST /edge-promote/...` → `{ok, edge, mode:"gated"}` then
  subsequent `a→b` routes gated. Both guarded by `_PATH_PARAM_RE` (`^[A-Za-z0-9_\-]+$`),
  return 400 on bad param, 500 on unexpected. Slot→id resolution walks `reversed(topologies)`
  (honors Assumption #3 "latest topology governs"). `test_edge_promote_gates_subsequent_sends`
  verifies the real broker behavior change, not just the response shape. ✓
- **AT#13 (multi-instance):** `_handle_edge_log` / `_handle_edge_promote` route locally when
  `crew_id == self._own_crew_id()`, else `_proxy_edge_log` / `_proxy_edge_promote` look the
  crew up in `InstanceRegistry` and proxy to its port (404 unknown crew / no registry, 502
  unreachable). Mirrors the established `_proxy_artifact` / `_proxy_shape_approval` pattern.

  **CLAUDE.md trap defeated — verified genuine.** `test_leader_proxies_edge_log_to_follower`
  stands up a *real* follower `UIServer.serve()` on a free port, registers it, then hits the
  leader (whose broker has **no topology**) via in-process ASGI. A 200 with ≥1 message can
  only come from the proxy reaching the follower — a same-origin-relative/local-only handler
  would 404 here. This is a true leader→follower exercise, not a single-instance stand-in. ✓

### Half B — on-graph SVG overlay (`claude_crew/ui/dashboard.html`) — deferred TEST, not deferred DELIVERABLE

The coordinator's earlier catch (first attempt omitted the overlay) is corrected. The overlay
is genuinely present and code-sound — and per spec the *test* is out-of-scope, not the artifact,
so its presence is part of adherence here (same principle that flipped the sibling scoped-send slice).

- `TopologyEdgePanel` (dashboard.html:1565) **IS an on-graph SVG-walk decoration**, not a
  side-table: builds `graph TD` from `edgeStats`, `mermaid.render` → DOMPurify (same config as
  `renderMermaidBlocks`, foreignObject allowed / script+handlers forbidden) → walks
  `svgEl.querySelectorAll('.flowchart-link, path.edge-path')` post-render. ✓
- **Re-applies after each render** via `useEffect([mermaidSrc, crewId])` — mermaid regenerates
  the SVG, decoration follows; same contract as the foreignObject legibility fix. Old click
  listeners die with the replaced SVG nodes (`innerHTML=''` + `appendChild`) — no leak. ✓
- Per-mode coloring (direct green / tee blue / gated amber / tripped red), pulse on
  `exchanges > prev` (stroke widens then `setTimeout` restores), tripped stroke-width, mode
  legend, selected-edge panel with message log. ✓
- **Multi-instance correct:** both `fetch` URLs carry `crewId` —
  `/edge-log/${crewId}/...` and `/edge-promote/${crewId}/...`. Promote control gated to
  non-gated/non-tripped edges. ✓
- Wired live: `<TopologyEdgePanel edgeStats={cli.topology_edge_stats} crewId={cli.id}/>` mounted
  inside `MiniGraph` (dashboard.html:1836); renders `null` when `edgeStats` empty. ✓

## Check 2 — Non-regression (independent re-run)

```
uv run pytest tests/test_edge_dashboard.py tests/test_edge_routing.py tests/test_circuit_breaker.py
→ 53 passed in 1.43s
```
17 slice tests + 36 sibling tests all green. Two warnings are pre-existing `websockets` deprecation
notices (uvicorn/websockets legacy), unrelated to this change. No sibling source files touched
(diff = `M ui_server.py`, `M ui/dashboard.html`, `?? tests/test_edge_dashboard.py`).

## Check 3 — Code-quality smoke (changed files)

- ui_server.py: handlers mirror existing proxy idioms; port validation rejects bool/non-int/out-of-range
  before building the URL; broad `except` → 500 with `_logger.exception`; proxy failures → 502. Clean.
- dashboard.html overlay: deterministic source ordering means `links[i]→edgeStats[i]` (no brittle ID
  parsing); `.catch(()=>{})` on `mermaid.render` tolerates un-parseable slot names; DOMPurify reused
  verbatim. No smells rising to High.
- Minor (non-blocking): `links[i]→edgeStats[i]` positional mapping assumes mermaid emits one
  `.flowchart-link` per source edge in source order. Robust for the `graph TD` shapes here; if a future
  edge-label form makes mermaid coalesce/reorder edges the mapping could skew. Not a defect today —
  noting for the backlog. The browser path is Playwright-verified per spec, outside this gate.

## Severity tally
No Critical, no High. One Low (positional edge-mapping assumption, informational).

---

RR-VERDICT: PASS m2-edge-routing 0 /home/jerome/dev/claude-crew/.rr-worktrees/m2-edge-routing/.rr/reports/m2-edge-routing-task-dashboard-edge-observability-slice-review-0.md
