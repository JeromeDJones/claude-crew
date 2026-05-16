# Doc-Sync Checklist: fidelity-audit-followups

> Output contract for the coordinator-driven doc-sync sub-step. The
> coordinator computes a candidate change-list against the five target
> files, surfaces each candidate to the user for accept / edit / skip,
> and records the per-file outcome here. Accepted edits are written via
> Write/Edit and staged. The conditional commit
> `docs: sync for fidelity-audit-followups [retro PASS]` only lands if
> `git -C <worktreePath> diff --cached --quiet` is false after staging.

- **Slug:** `fidelity-audit-followups`
- **Worktree:** `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups`
- **Retro cycle:** 0

---

## Per-file Decisions

| File | Proposed Change | Outcome | Notes |
|------|-----------------|---------|-------|
| `doc/PRODUCT-VISION.md` | Append to row #27 notes cell: both deferred gaps closed 2026-05-16 by `fidelity-audit-followups`; pointer to new FEATURE file | accept \| edit \| skip | |
| `doc/ROADMAP.md` | skipped — file absent | skip | `doc/ROADMAP.md` does not exist in the worktree; out of scope to create |
| `doc/features/FEATURE-fidelity-audit-followups.md` | Create new file documenting what this slice shipped and the gaps it closed | accept \| edit \| skip | |
| `doc/BACKLOG.md` | Mark the two `[2026-05-16] #27` sub-entries for `cost-telemetry-zero` and `yaml-loader-bypass` as closed | accept \| edit \| skip | |
| `doc/ARCHITECTURE.md` | skipped — file absent | skip | `doc/ARCHITECTURE.md` does not exist in the worktree; out of scope to create per skill contract |

---

## Row 1 — `doc/PRODUCT-VISION.md`

**Kind:** edit

**Path:** `doc/PRODUCT-VISION.md`

### Details

Row #27 (line 202) currently ends its notes cell with:

```
...Two medium deferred gaps: AT8 yaml-loader-bypass (dispatch asserted, not loader) + cost-telemetry-zeros (artifact fields present, all zeros). See `doc/features/FEATURE-fidelity-audit-suite.md`. |
```

Proposed replacement (append before the closing ` |`):

```
...Two medium deferred gaps: AT8 yaml-loader-bypass (dispatch asserted, not loader) + cost-telemetry-zeros (artifact fields present, all zeros). Both closed 2026-05-16 by `fidelity-audit-followups`: `discover_dir` extended to `*.yaml`/`*.yml`; AT8 refactored through `build_merged_pack` end-to-end; `_record_sdk_cost` wires real SDK spend into cost artifact (7/9 live tests non-zero). See `doc/features/FEATURE-fidelity-audit-suite.md` and `doc/features/FEATURE-fidelity-audit-followups.md`. |
```

### Rationale

Row #27 is the canonical pipeline record for the fidelity-audit suite. It ships with two Medium deferred gaps named inline. This slice's entire purpose was to close them. The notes cell is the right place to record closure — same pattern used for other "done with follow-up" features. The status cell (`done (2026-05-16)`) is unchanged; the gaps were resolved on the same calendar date.

---

## Row 2 — `doc/ROADMAP.md`

**Kind:** skip

**Path:** `doc/ROADMAP.md`

### Details

File does not exist in the worktree.

### Rationale

Out of scope to create per skill contract ("skip if absent — out of scope to create").

---

## Row 3 — `doc/features/FEATURE-fidelity-audit-followups.md`

**Kind:** create

**Path:** `doc/features/FEATURE-fidelity-audit-followups.md`

### Details

Create new file with the following content:

```markdown
# Feature: Fidelity Audit Followups

**Status**: Shipped (2026-05-16)
**Created**: 2026-05-16
**Parent feature**: #27 — `doc/features/FEATURE-fidelity-audit-suite.md`

---

## Problem

#27 shipped with two semantic gaps (Medium severity) that weakened the CLI-fidelity moat:

1. **`cost-telemetry-zero`** — the autouse cost fixture wrote JSONL records but every live
   test stored `{input_tokens:0, output_tokens:0, cost_usd:0.0}`. AT11 passed (field
   presence) but the artifact's purpose — a real per-run cost record — was defeated.
2. **`yaml-loader-bypass`** — `TestAgentFormatYamlPolymorphism` manually constructed the
   YAML-side `AgentDefinition` via `yaml.safe_load`; `discover_dir` globbed `*.md` only.
   AT8 asserted dispatch fidelity, not loader-side YAML support.

## What Shipped

### Cost telemetry (cost-telemetry-zero)

New helper `_record_sdk_cost(broker, tid, *, result_msg=None)` added to
`tests/test_fidelity_audit.py`. Reads `total_input_tokens`, `total_output_tokens`,
`total_cost_usd` from `SdkTeammate.status_snapshot()` — already populated from
`ResultMessage.usage` per F14 at `sdk_teammate.py:1170-1181`. Called from each of the
7 live-SDK test classes after the reply envelope arrives. The auth-failure class
remains at zeros (no real SDK call — structurally correct per spec).

Two `TestHookFiringFidelity` cases use `ClaudeSDKClient` directly (no broker handle)
and inline their own `ResultMessage.usage` extraction. The helper signature accepts an
optional `result_msg=` so both paths write through the same function with uniform
JSONL semantics.

### YAML loader (yaml-loader-bypass)

`discover_dir` in `claude_crew/subagents/_user_loader.py` extended: now globs `*.md`,
`*.yaml`, and `*.yml`. README exclusion, size/count caps, and alphabetical sort
preserved across the combined set. Mixed-stem collision (`.md` + `.yaml` with same
stem → same kebab key) emits WARN and alphabetical-later wins, matching existing
same-format collision behavior.

New `parse_yaml_pack_text(text: str, path: Path)` function added to
`claude_crew/subagents/_loader.py`. The pure-YAML document IS the frontmatter mapping;
body read from `prompt_body` field. Reuses `_validate_frontmatter` + `AgentDefinition`
construction. `strict_parse` refactored to read YAML files once, threading `text`
through rather than re-reading (eliminates the double-I/O asymmetry flagged at slice
and feature review).

`TestAgentFormatYamlPolymorphism` refactored: removed manual `yaml.safe_load` block;
both `agent_md_name` and `agent_yaml_name` now enter `merged_pack` via
`build_merged_pack` before any assertion. AT8 now asserts loader-side YAML support,
not only dispatch-side.

### Apply-in-feature fixes (shipped in `00bdd74` after retro)

Routing category A–E from the feature retro, applied as a single post-retro commit:

- **A** — hex sentinels shortened 32→12 chars in `TestBundledPackDispatchFidelity` and
  `TestAgentFormatYamlPolymorphism` (LLM relay boundary structural flake; cycle-0 retry)
- **B** — `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (3 sites); inline
  imports in `test_both_formats_dispatchable` hoisted to module top
- **C+E** — `parse_yaml_pack_text` API made symmetric with `parse_pack_text(text, path)`;
  `strict_parse` single-read; `_user_loader.py` call sites updated
- **D** — `_record_sdk_cost` overloaded to accept `result_msg=None` so both extraction
  paths (snapshot-driven + ResultMessage direct) write through the same helper

## Validation Result

```
10 passed, 1 xfailed in 76.05s
```

Pass criteria all met:

- ✅ `10 passed, 1 xfailed` (matches #27 baseline; no new failures)
- ✅ `tests/_artifacts/fidelity-audit-cost.jsonl` — 7/9 lines non-zero
- ✅ `grep -c "yaml.safe_load"` inside `TestAgentFormatYamlPolymorphism` → 0
- ✅ Wall time 76s < 150s (2× #27 baseline)
- ✅ Real SDK spend ~$0.35 (within spec budget)

## Files Changed

| File | Change |
|---|---|
| `claude_crew/subagents/_user_loader.py` | `discover_dir`: `*.yaml`/`*.yml` glob added; kebab-collision logic for format-mixed stems |
| `claude_crew/subagents/_loader.py` | `parse_yaml_pack_text(text, path)` added; `strict_parse` single-read refactor |
| `tests/test_fidelity_audit.py` | `_record_sdk_cost` helper; AT8 refactored through `build_merged_pack`; 7 live classes wired; apply-in-feature fixes A–D |
| `tests/test_user_loader.py` | `discover_dir` YAML tests (AT-3/AT-4/AT-7/AT-8) — default-CI, no SDK spend |

## Known Gaps Closed

Both Medium gaps from `doc/features/FEATURE-fidelity-audit-suite.md § Known Gaps`:

| Tag | Was | Now |
|---|---|---|
| `spec-satisfaction.yaml-loader-bypass` | `discover_dir` globs `*.md` only; AT8 bypasses loader | `discover_dir` globs `.md`/`.yaml`/`.yml`; AT8 routes through `build_merged_pack` |
| `spec-satisfaction.cost-telemetry-zero` | All live tests write `{input_tokens:0, …}` | `_record_sdk_cost` wires real SDK spend; 7/9 live tests produce non-zero artifact lines |

Remaining open gap (`cracks.hook-test-bypasses-sdkteammate`) is unchanged — out of
scope for this slice.

## Process Notes

- **3 breakout-review cycles** before PASS — AT-1 ownership drift, Invariant-2 violation,
  schema enum mismatch. `implementationKind` enum and its interaction with AT isolation
  need pre-emptive documentation in the breakout template (routed to FDE BACKLOG).
- **Cycle-0 hex-relay flake** on task-2 validation — pre-existing 32-char sentinel
  truncation; fixed in apply-in-feature A. Adds ~$0.35 cycle cost when it fires.
- **`rr-slice-reviewer` death** on task-2 (validation-only diff, ~955k then ~510k
  tokens) — coordinator-authored substitute. Routed to FDE BACKLOG
  `[2026-05-16] Per-task slice-reviewer lifecycle change`.
- **Total spend ~$1.05** (three live runs: cycle-0 flake retry + cycle-1 + standalone
  validation). Spec budgeted $0.35 for a single clean run.
```

### Rationale

The canonical target for this slug is `FEATURE-fidelity-audit-followups.md`. Creating a new file rather than amending `FEATURE-fidelity-audit-suite.md` keeps the parent feature doc's Known Gaps section as written history (the gaps existed at #27 ship) and adds a forward-pointer from PRODUCT-VISION row #27. The parent feature doc's Known Gaps section (`FEATURE-fidelity-audit-suite.md`) should ideally be updated to mark both gaps closed — but that file is not in the canonical five targets for this slug; the coordinator should apply that edit manually or in a subsequent pass.

---

## Row 4 — `doc/BACKLOG.md`

**Kind:** edit

**Path:** `doc/BACKLOG.md`

### Details

Under `## [2026-05-16] Feature: fidelity-audit-suite (#27)`, two sub-entries should be marked closed. Proposed edits:

**Sub-entry 1 heading** — find:
```
### Wire `ResultMessage.usage` extraction into each live test body
```
Replace with:
```
### Wire `ResultMessage.usage` extraction into each live test body — CLOSED by fidelity-audit-followups (2026-05-16)
```

**Sub-entry 2 heading** — find:
```
### Extend `discover_dir` to discover `*.yaml`/`*.yml` agent files (AT8 yaml-loader gap)
```
Replace with:
```
### Extend `discover_dir` to discover `*.yaml`/`*.yml` agent files (AT8 yaml-loader gap) — CLOSED by fidelity-audit-followups (2026-05-16)
```

No new BACKLOG entries from this retro. All lessons-for-future-slices items were either:
- Applied in-feature (A–D, shipped in `00bdd74`)
- Routed to `~/dev/FDE/doc/BACKLOG.md` (RepoReactor process concerns: breakout-schema invariants, per-task slice-reviewer lifecycle, `git add -f .rr/`, `state-op.sh` setter)
- Observations captured in the retro itself (total spend, cycle counts)

### Rationale

The two sub-entries were the explicit motivation for this slice. Leaving them open after the slice ships creates stale BACKLOG debt. The standard closure pattern for sub-entries in this file is inline "— CLOSED by X (date)" appended to the `###` heading. The parent section header `## [2026-05-16] Feature: fidelity-audit-suite (#27)` stays intact — the `_note` paragraph at the end of that section remains accurate history.

---

## Row 5 — `doc/ARCHITECTURE.md`

**Kind:** skip

**Path:** `doc/ARCHITECTURE.md`

### Details

File does not exist in the worktree.

### Rationale

Skill contract: "If `doc/ARCHITECTURE.md` does not exist in the worktree, skip this row cleanly: do not propose to create the file." No architectural change this feature touches a file that would need to be captured here anyway — the two changes (`discover_dir` glob extension, `parse_yaml_pack_text` in `_loader.py`) are localized loader-layer additions with no cross-module reshape.

---

## Outcome

mixed — see per-row decisions

Rows 1, 3, 4 carry candidate edits/creates. Rows 2 and 5 are skip (files absent).

## Staged Diff Summary

_no staged changes_ — this is a doc-sync checklist only. The coordinator applies the accepted edits via Write/Edit and stages them. Run `git -C <worktreePath> diff --cached --stat` after applying.

---

## Outcome — user triage applied 2026-05-16

| # | File | Triage | Applied |
|---|------|--------|---------|
| 1 | `doc/PRODUCT-VISION.md` | accept (edit) | ✓ row #27 notes updated — both gaps closed by `fidelity-audit-followups`, pointer to updated FEATURE doc's gaps table |
| 2 | `doc/ROADMAP.md` | skip (file absent) | — |
| 3 | `doc/features/FEATURE-fidelity-audit-followups.md` | **user-skipped** | — (user chose to fold updates into the existing #27 FEATURE doc instead of creating a sibling) |
| 4 | `doc/BACKLOG.md` | accept (edit) | ✓ both `[2026-05-16] #27` sub-entries marked CLOSED |
| 5 | `doc/ARCHITECTURE.md` | skip (file absent) | — |
| 6 | `doc/features/FEATURE-fidelity-audit-suite.md` | accept (edit, non-canonical) | ✓ Known Gaps table strikethrough + closure note for both Mediums |

Staged for the `docs:` commit gate.
