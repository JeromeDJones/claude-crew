# Doc-Sync Checklist — m3-5-reshape-live-crew (cycle 0)

**Feature:** `m3-5-reshape-live-crew`  
**Retro cycle:** 0  
**Documenter verdict:** PASS

---

## Pre-sync coherence check

The `reshape-docs-sync` implementation slice (cycle 0) already applied the primary doc updates before this retro. The docs-sync slice review verified AT-20 passes (2 passed), all doc claims cross-check against code, and no stale conditional-wiring prose remains. The feature-review confirms: "both docs describe [D0] identically." Starting state for this retro was therefore coherent and complete for the core M3.5 deliverables.

---

## Edits auto-applied by this retro

### 1. `doc/ARCHITECTURE.md` — add `test_live_reshape.py` to live-test file list

**Section:** `## Test Conventions`  
**Gap:** The live test file `tests/test_live_reshape.py` (added by the `live-reshape-regression-tests` task, covering AT-17/18) was not listed alongside the other gated live-test files. A developer consulting the test conventions would not know this file requires `CLAUDE_CREW_LIVE_TESTS=1`.

**Edit applied:**
```diff
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_live_stderr.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
+ Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_live_stderr.py`, `test_live_reshape.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
```

**Status:** ✅ Applied

---

### 2. `doc/ARCHITECTURE.md` — add live peer-delivery edge-mode convention

**Section:** `## Test Conventions` (after the free-TCP-port bullet)  
**Gap:** The validation report identified a high-signal institutional lesson: live tests asserting peer delivery initially timed out because they used gated edges (ShapeEdge default), which route to the coordinator's inbox, not the recipient's. This is the first live test to exercise real teammate `send_to` since M2 (M2 tested broker-side routing at stub level only). The lesson is not captured anywhere in the project docs.

**Edit applied:** Added new bullet:
> **Live tests asserting teammate→teammate peer delivery must use `direct` edges.** Gated edges (the `ShapeEdge` default) route messages to the coordinator's inbox via `broker._send_routed`, not the recipient's inbox — a peer-delivery assertion against a gated edge will time out rather than fail fast. Verified 2026-06-30 (`test_live_reshape.py` AT-17/18): initial runs timed out because the test fixture used the default gated mode; switching to `direct` edges resolved it. See also [`m3-5-reshape-live-crew` validation report].

**Status:** ✅ Applied

---

### 3. `CLAUDE.md` — add `test_live_reshape.py` to live-test file list

**Section:** `### Test conventions`  
**Gap:** Same as ARCHITECTURE.md edit 1. The `test_live_reshape.py` file was absent from the listed live-test files. (Note: CLAUDE.md also does not list `test_live_stderr.py` — pre-existing inconsistency, not introduced by this feature and left in scope for a separate cleanup.)

**Edit applied:**
```diff
- Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
+ Live SDK tests (`test_live_sdk.py`, `test_live_subagents.py`, `test_live_reshape.py`, `test_user_loader_live.py`) are skipped unless `CLAUDE_CREW_LIVE_TESTS=1`.
```

**Status:** ✅ Applied

---

### 4. `CLAUDE.md` — add live peer-delivery edge-mode convention

**Section:** `### Test conventions` (after the "Validate the whole suite" bullet)  
**Gap:** Same institutional lesson as ARCHITECTURE.md edit 2. Both canonical doc files should carry this convention since both are read as primary references.

**Edit applied:** Added new bullet:
> **Live tests asserting teammate→teammate peer delivery must use `direct` edges.** Gated edges (the `ShapeEdge` default) route messages to the coordinator's inbox via `broker._send_routed`, not the recipient's inbox — a peer-delivery assertion against a gated edge will time out rather than fail fast. Verified 2026-06-30 (`tests/test_live_reshape.py` AT-17/18): initial live runs timed out because test fixtures used the default gated mode; switching to `direct` edges resolved it. No prior live test had exercised a real teammate `send_to` before M3.5 — M2 only validated the broker-side routing at stub level.

**Status:** ✅ Applied

---

## Target file inventory

| File | Status | Notes |
|------|--------|-------|
| `doc/ARCHITECTURE.md` | ✅ Updated | 2 edits applied (live-test list + peer-delivery convention); all M3.5 content (reshape_crew tool, D0, M3.5 workflow section, broker helpers, scoped send_to) was already in place from reshape-docs-sync slice |
| `CLAUDE.md` | ✅ Updated | 2 edits applied (live-test list + peer-delivery convention); reshape_crew in core-components, D0 in sdk_teammate.py section, tools:[] addendum already in place |
| `doc/BACKLOG.md` | ⬜ No change | Not touched; backlog candidates are in the backlog-candidates artifact, not directly written here |
| `README.md` | ⬜ No change | No end-user-facing content about reshape_crew is missing; high-level orchestrator description unchanged |
| `doc/sdk-teammate-wiring.md` | ⬜ No change | D0 is a claude-crew internal change (SdkTeammate tool wiring), already covered in ARCHITECTURE.md + CLAUDE.md; no addition warranted |

---

## Omitted from auto-apply (rationale)

### D2 broker helpers absent from `broker.py` module table in `ARCHITECTURE.md`

The three new M3.5 broker helpers (`latest_topology`, `set_edge_override`, `remove_edge_overrides`) are documented in the "Workflow Shape Composition (M3.5)" narrative section of ARCHITECTURE.md but not in the per-module `broker.py` table. The broker.py section has an explicit "M2 additions" table; by structural analogy one might add an "M3.5 additions" table.

**Reason not applied:** The helpers ARE documented (in the M3.5 workflow prose section, alongside their usage context). Adding them to the broker.py table would be duplication. The docs-sync slice reviewer passed without flagging this gap, confirming the prose-section placement is intentional. If a separate "M3.5 additions" broker table is desired, it belongs in a backlog item, not a retro auto-apply.

### AT-20 staleness guard for `CLAUDE.md`

Not an ARCHITECTURE.md edit but a test file (`test_reshape_docs_staleness.py`) gap. Captured as BC-3 in the backlog-candidates artifact. Doc-sync does not modify test files.

---

## Post-sync verification

Both edited files remain internally consistent:

- **ARCHITECTURE.md:** `test_live_reshape.py` is now listed with other live tests; the new convention bullet is the natural next entry after the free-TCP-port bullet; no content is duplicated.
- **CLAUDE.md:** `test_live_reshape.py` is listed with other live tests; the new convention bullet follows "Validate the whole suite" and precedes "### SDK behavior — verified invariants"; parallel to the ARCHITECTURE.md entry (same verified date, same root cause, consistent phrasing).
- AT-20 (`test_reshape_docs_staleness.py`) still passes: the edits do not re-introduce `_has_out_edges` in either file.

---

## Backlog candidates artifact

→ `/home/jerome/dev/claude-crew/.rr-worktrees/m3-5-reshape-live-crew/.rr/reports/m3-5-reshape-live-crew-retro-backlog-candidates.md`

5 items (BC-1 through BC-5). BC-5 is auto-applied here; BC-1 through BC-4 are BACKLOG.
