# Feature Review: agent-pack-refresh

**Verdict:** PASS
**Cycle:** 0
**Scope:** cross-slice synthesis of `pack-state-holder` + `refresh-method-and-diff` + `mcp-refresh-agents-tool`.

Slice reviews already covered per-task adherence; this review focuses on integration coherence, holistic spec satisfaction (including the carried per-source-counts ruling), and cracks-fell-through.

## Check 1 — Cross-Slice Integration Coherence

The three slices interlock cleanly:

- **Holder ⇄ read-sites.** `_PackState` (slice 1) is the single mutable seam. All three sdk-mode read sites — `factory()` (factories.py:414–416), `_resolve_role` (factories.py:386), `_resolve_agent_def` (factories.py:521) — reach `holder.pack` / `holder.role_ss` / `holder.bodies` *live* on every call. The atomic swap in `_PackState.refresh()` (factories.py:242–245) consequently propagates immediately to subsequent spawns and to the broker's per-spawn `AgentDefinition` snapshot without re-wiring closures. <feature.integration>coherent</feature.integration>
- **Refresh ⇄ tool surface.** `factory.refresh_pack = holder.refresh` (factories.py:509) and stub-side `stub_factory.refresh_pack = _stub_refresh_pack` (factories.py:80) give `make_server` a uniform attribute to dispatch to. The `refresh_agents` tool (server.py:531–561) calls it via `getattr(factory, "refresh_pack", None)` with a structurally-identical fallback dict — the fallback even reaches the same `_REFRESH_NOTE` sentinel so the contract stays single-sourced.
- **`_REFRESH_NOTE` deduplication.** Stub path, sdk path, and server fallback all return the same literal note string by importing/referencing the module-level constant. No risk of drift between the docstring assertion and `result["note"]`. <feature.integration>coherent</feature.integration>
- **Diagnostics capture parity.** `_PackState.refresh()` reuses `collect_startup_diagnostics()` + `_direct_attach_fallbacks()` (factories.py:178–192) — the same envelope used by `default_factory()` at startup (factories.py:336–356). The propagation-probe + level-restore is symmetric across both call sites, so refresh warnings have identical semantics to startup warnings. <feature.integration>coherent</feature.integration>
- **Lock discipline vs reads.** Readers are unlocked (single-attribute reads on a dataclass are CPython-atomic); writer acquires the lock for the three-field swap. A spawn arriving mid-refresh observes either fully-pre or fully-post state — never a torn mix across `pack`/`role_ss`/`bodies`. This is the only ordering invariant the spec asserts, and it holds.

## Check 2 — Holistic Spec Satisfaction

All 11 acceptance tests have direct coverage per the two slice-review reports. Beyond that, the **promised user outcome** ("operator edits a project agent file on a running server, calls refresh_agents, and a subsequently-spawned teammate runs with the edited definition") is realised end-to-end:

- Project-layer add → `diff.added` + resolver visible (AT-1, AT-9 via MCP transport).
- Project-layer remove → `diff.removed` (AT-2).
- Project-layer edit → `diff.changed` via `dataclasses.asdict` field comparison (AT-2, factories.py:220–224).
- User-layer add → `diff.added` (AT-7, proving four-layer re-merge).
- Malformed file → ok=True, warning surfaces the filename, prior valid roles spawnable (AT-8, AT-11).
- Rebuild raise → ok=False, prior pack preserved (AT-4).
- Cwd footgun avoided — captured `home_dir`/`project_root` honoured against monkeypatched cwd (AT-6).
- Stub-mode parity (AT-10).

Out-of-scope items (Broker.startup_diagnostics resync, file-watching, dry-run, per-cwd) are correctly *not* attempted.

### Required ruling: per-source counts (default/user/project always 0)

**Ruling: (a) Acceptable for v1 — ship — but log a Medium doc-accuracy finding requiring the over-promise to be corrected.**

Reasoning:

1. **The load-bearing operator signal is the `diff`, not `counts`.** When an operator runs `refresh_agents` after editing a file, what they need is "did my change land, and what else changed?" The `diff.added/removed/changed` answer that exactly. `counts.total` is also correct and serves as a sanity check. The `default/user/project` per-layer breakdown is a nice-to-have telemetry view that would inform "where did this role come from" — but it's not on the critical path for the v1 user story, and the diff already carries the answer they actually need on a refresh cycle.
2. **Recovering per-layer attribution is a real refactor, not a one-liner.** `build_merged_pack` returns the merged dict only; per-layer attribution would require either (a) re-loading each layer separately and counting before merge (doubles IO + duplicates precedence logic), or (b) threading per-layer counts out of `build_merged_pack` as a new return-shape (cross-cuts the loader's signature, which is consumed at startup too). Spec correctly identifies this and the implementor correctly deferred rather than papering over.
3. **The plugin count IS correct** (derived from `:` in keys, factories.py:230), so the field structure is honest about *what* it can compute today.
4. **However**, two surfaces over-promise and must be corrected for honesty:
   - `server.py:543` — `refresh_agents` docstring says `counts: Post-refresh pack counts per layer (default/plugin/user/project/total)`. This reads as "all five are real counts." It is not.
   - Spec `Data / API Contracts` block (lines 47–49) shows the same shape with no caveat.

   The hollow zeros silently lie to an operator who trusts the documented contract. This is a contract-honesty issue worth fixing on a follow-up, but does not invalidate the feature: the diff is the truthful operator signal.

**Severity assessment:** Medium. Not a regression, not a security issue, not a behavior break. A doc/contract-honesty cleanup. Per the three-check charter, Medium does **not** block PASS.

**Recommended follow-up (not blocking):** either (i) amend the `refresh_agents` docstring + spec to flag `default/user/project` as "reserved for future per-layer attribution; currently always 0", or (ii) plumb per-layer counts out of `build_merged_pack` and populate them honestly. (i) is the v1-honest move; (ii) is the v2 fill-in.

## Check 3 — Cracks-Fell-Through

Items the per-slice gates couldn't see by construction:

- **No multi-instance dashboard implications.** Refresh is per-broker; no new lazy-fetch endpoint, no new per-row data path. The CLAUDE.md leader/follower trap does not apply. <feature.completeness>covered</feature.completeness>
- **No broker-state mutation.** `Broker.startup_diagnostics` is deliberately not updated, per spec Out of Scope; refresh warnings instead surface in `RefreshResult.warnings`. The dashboard Startup Notices panel therefore continues to reflect *startup* state — a deliberate, documented choice. No silent contract drift. <feature.completeness>covered</feature.completeness>
- **Future-spawns-only contract is double-sourced** (tool docstring + `RefreshResult.note`), both pointing at the same `_REFRESH_NOTE` constant. An operator reading either gets the same statement.
- **Auto-promotion (`_resolve_role`) post-refresh.** Reads `holder.pack` live; an added plugin-namespaced role becomes auto-promotable on the next spawn. Removed roles fall through to the existing unknown-role path. Consistent behavior. <feature.completeness>covered</feature.completeness>
- **`_resolve_agent_def` post-refresh.** Same live-read; the broker's per-spawn config snapshot uses the *current* holder state, so a teammate spawned after refresh gets a dashboard chip that matches its actual `AgentDefinition`. <feature.completeness>covered</feature.completeness>
- **Lock scope.** Only the three-field swap is in the critical section; the rebuild (slow IO) runs outside the lock, so a hung loader can't deadlock spawns. Correct.
- **`_PackState._lock` and `dataclasses.dataclass`.** A `threading.Lock()` as a `default_factory` dataclass field is non-trivial; verified slice 1 used `dataclasses.field(default_factory=threading.Lock)` (factories.py:152) — correct.

Minor smells (non-blocking, info):

- `server.py:552` — `from claude_crew.factories import _REFRESH_NOTE` inside the fallback branch reaches a module-private name and is a late import. Acceptable today (the fallback should not fire in normal operation), but if `_REFRESH_NOTE` ever has to evolve, this becomes a drift surface. Hoist to a public constant on a future pass.
- Slice-3 test imports placed mid-file rather than at the top per CLAUDE.md test conventions. Style nit, already called out by slice review.

## Findings

| Severity | Tag | Finding |
|----------|-----|---------|
| Medium | `feature.completeness` | `refresh_agents` tool docstring (`server.py:543`) and spec `Data / API Contracts` shape advertise `counts.default/user/project` as real per-layer counts; in v1 they are hard-zero. Either annotate as reserved or populate honestly. Not blocking; follow-up. |
| Info | `feature.style` | `_REFRESH_NOTE` reached via private import in `server.py` fallback branch. Consider promoting to a public constant. |
| Info | `feature.style` | Slice-3 test imports below module header; hoist on future pass. |

- Critical: none
- High: none
- Medium: 1 (doc/contract honesty on per-layer counts — non-blocking per charter)
- Info: 2

## Verdict Rationale

Integration coherence is clean — one holder, live reads everywhere, single-sourced note, symmetric diagnostic capture across startup and refresh. Holistic spec satisfaction is met for every acceptance criterion and for the promised operator outcome. The one carried gap (per-layer counts) is a documented v1 limitation with the truthful operator signal (`diff`) intact; the only remediation needed in v1 is doc-accuracy, which is Medium and explicitly non-blocking. No Critical or High findings.

RR-VERDICT: PASS agent-pack-refresh 0 /home/jerome/dev/claude-crew/.rr-worktrees/agent-pack-refresh/.rr/reports/agent-pack-refresh-feature-review-0.md
