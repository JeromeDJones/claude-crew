# FEATURE — Plugin agent config visibility on the dashboard

**Status:** Phase 1 (research / spike)
**Filed:** 2026-05-07
**Surfaced by:** Operator report — running claude-crew in a project where `repo-reactor` was installed at project scope (`scope: "local"`, `projectPath: <repo>`); spawned a `repo-reactor:*` teammate; teammate ran fine; dashboard config panel showed no agent-definition details (tools, skills, model, system prompt).

---

## Phase 1 — Problem statement

A teammate spawned from a **project-scope plugin install** runs successfully but the dashboard config panel for that teammate is empty. The operator-visible symptom is "no chips, no expanded detail." The teammate itself functions; only the *transparency surface* is broken.

### What we know

- Operator did **not** pass `extra_tools` or `extra_skills`. Pure pack-shaped spawn.
- The plugin (`repo-reactor`) was installed at `scope: "local"` with `projectPath` matching the cwd claude-crew was started in.
- Plugin agent files have full frontmatter (`description`, `tools`, `model`, `skills`, `memory`). Verified locally — `build_merged_pack()` loads them with all fields populated and namespaced as `repo-reactor:<role>`.
- Operator described the missing data as the "agent definition was not present at all" — i.e. the broker's `_configs[teammate_id]` is `None` (or the resolver returned `None`), not "present but empty fields."
- Tests pass; no existing test covers the project-scope plugin path end-to-end through `agent_def_resolver` → `_snapshot_config` → `BrokerSnapshot.live[*].config` → `/api/state` → dashboard panel.

### What we suspect

We don't have a confirmed root cause. Three live hypotheses:

**H1 — `_resolve_agent_def` divergence from spawn factory.**
`factories.py:294-301` — when a role is *not* in `merged_pack` and extras are passed, the spawn factory constructs a **synthetic** `AgentDefinition` and the teammate runs. `factories.py:362-365` — `_resolve_agent_def` returns `None` in the same case. Result: alive teammate, null config snapshot. Discovered by code reading. **Likely a real bug regardless of whether it's the operator's actual case** — the operator reported no extras, so this divergence is not the symptom they hit, but the asymmetry should be eliminated.

**H2 — `_resolve_role` collision or shadow miss for namespaced plugin keys.**
The factory's `_resolve_role` promotes bare names to `<plugin>:<role>` when the bare name isn't in the pack and exactly one namespaced match exists. The broker stores the *original* role string in `TeammateInfo.role` while `agent_def_resolver` re-resolves and looks up. If the spawn-time role string is already namespaced (e.g. `repo-reactor:rr-planner`), `_resolve_role` returns it as-is and `merged_pack.get()` should hit. Verified locally — namespaced lookup works against a hand-built pack. The hypothesis stays open until we trace the operator's exact spawn call.

**H3 — Project-root resolution mismatch between MCP startup and spawn time.**
`build_merged_pack()` resolves `project_root` once at MCP-server startup; plugin local-scope installs are filtered by `projectPath == project_root`. If claude-crew was started from a different cwd than the operator thought (e.g. via shell drop-in, IDE-launched, or a wrapped invocation), the project-scope plugin pack would simply not load — `merged_pack` would have no `repo-reactor:*` entries — and the spawn factory's `_resolve_role` would fall through with the original namespaced string, which is then absent from `merged_pack`. **Open question:** if the role isn't in `merged_pack`, how did the teammate spawn successfully? Possible answers: (a) the SDK accepts an unknown role and falls back to default config; (b) some other code path supplies the AgentDefinition. Needs tracing before we can rule out.

### Why it matters

The config panel is the only first-party introspection surface for "what is this teammate actually configured with?" When it lies (or is empty) for a class of agents the operator legitimately spawns, downstream debugging gets much harder — operators end up reading pack files by hand to confirm what the substrate already knows.

The "transparency floor" promise from #18 / #25 fails for project-scope plugin agents specifically. We need to either (a) make it work or (b) make the failure visible (a startup diagnostic, an explicit "no AgentDef resolved" badge, etc.) instead of silently rendering an empty panel.

### Out of scope (for this spec)

- Behavior changes to plugin loading itself (file discovery, `installed_plugins.json` semantics, scope filter rules).
- New plugin-related features. This is a fix-and-make-visible spec, not a feature spec.

---

## Phase 1 — Spike plan

Before Phase 2 we need to **reproduce** and **identify the actual root cause**. Plan:

1. **Repro on a known-broken setup.** Either: (a) get the operator's installed_plugins.json + cwd; (b) install `repo-reactor` at project scope locally and run claude-crew from that project. Spawn a `repo-reactor:rr-planner` teammate, capture the `/api/state` payload for the live teammate, and confirm `agents[*].config` is missing or null.
2. **Trace through.** With logging cranked, capture:
   - The role string passed to `broker.spawn_teammate`.
   - What `_resolve_role(role)` returns inside `_resolve_agent_def`.
   - Whether `merged_pack.get(resolved)` is `None` or an `AgentDefinition`.
   - The full `_snapshot_config` return value.
   - The `LiveEntry.config` field in `BrokerSnapshot`.
   - The `agent_entry["config"]` key presence on the wire.
3. **Identify the break point** — which transition drops the data.
4. **Decide fix scope** — single bug, or family. Two flavors already known:
   - H1 (synthetic-AgentDef divergence) is a real asymmetry worth closing regardless.
   - H3 (project-root mismatch silently dropping the plugin) might warrant a startup diagnostic — capture-via-#25's channel.

### Acceptance for the spike

- A failing test that reproduces the operator's symptom (live teammate, missing config) using the project-scope plugin path.
- A short root-cause writeup added inline to this doc (Phase 1 → Findings) before Phase 2 begins.

---

## Phase 1 — Open questions for Phase 2 gate

1. Is the fix one bug or a family? Phase 2 scope depends on the spike findings.
2. Should H1 (synthetic-AgentDef divergence) be folded into this spec, or split into a separate small fix? It's a real bug but not the operator's reported case.
3. If H3 is the cause, do we (a) auto-detect project mismatch and surface as a startup diagnostic, (b) document loudly, or (c) both?
4. Should the dashboard render an explicit "no AgentDef resolved" state for any teammate where config is `None`, distinct from "AgentDef present but all fields empty"? Today both render identically (panel is empty) and that ambiguity hid this bug.

---

## Phase 1 — Findings

**Summary:** Spike could not reproduce the reported symptom. Project-scope plugin agents load correctly, resolve properly, and render config panels on the dashboard. No bug found in the current codebase.

### Investigation approach

Wrote three end-to-end tests (`test_plugin_config_visibility_spike.py`) covering the three data-flow transitions where config could drop:

1. **Test: Bare-name spawn with project-scope plugin** — Spawn `rr-planner` (auto-promotes to `repo-reactor:rr-planner`). Config populated ✓
2. **Test: Namespaced spawn with project-scope plugin** — Spawn `repo-reactor:rr-planner` directly. Config populated ✓
3. **Test: Project-root mismatch scenario (H3)** — Simulates registration for project_a but resolution against project_b. Config populated when factory has correct pack ✓

All three pass. The merged pack loads the plugin agent, `agent_def_resolver` finds it, `_snapshot_config` builds the dict, and `BrokerSnapshot.live[*].config` is non-None on the wire.

### Root cause analysis

**Span traced (all transitions healthy):**

1. **Plugin load:** `_read_installed_plugins` correctly filters by scope (`local`) and `projectPath` match ✓
2. **Pack merge:** `load_plugin_agents` correctly namespaces keys as `<plugin_short>:<role>` ✓
3. **Role resolution:** `_resolve_role(role)` auto-promotes `rr-planner` → `repo-reactor:rr-planner` when bare name not found but single `*:rr-planner` exists ✓
4. **Agent lookup:** `merged_pack.get(resolved)` returns `AgentDefinition` ✓
5. **Config snapshot:** `_snapshot_config(agent_def, ...)` returns non-None dict with tools/skills/model ✓
6. **UI wire:** `BrokerSnapshot.live[*].config` serializes as `agent_entry["config"]` on the wire ✓

### Possible explanations

**Hypothesis 1 (H1 — synthetic-AgentDef divergence):** The spec mentions an asymmetry where spawn-path creates synthetic `AgentDefinition` when extras are passed but role absent from pack, while `_resolve_agent_def` returns `None`. This is a real code divergence but **not the operator's case** (no extras reported). **Status: separate bug, confirmed code pattern, but not this incident.**

**Hypothesis 2 (H2 — namespace mismatch):** Bare-name auto-promotion works correctly in tests. Namespaced spawn works. **Status: working as designed.**

**Hypothesis 3 (H3 — project-root mismatch):** Plugin registration is correctly filtered by project path. Factory captures project_root at startup; spawn uses that factory. **Status: expected behavior, not a bug unless factory is reused across projects.**

### Operator clarification (2026-05-07)

Operator confirmed the missing piece: the plugin was installed at a **project level above** where claude-crew was running — i.e. `projectPath` is a parent directory of the claude-crew cwd, not an exact match.

This is **H3 with a twist** that the spike's tests didn't cover. `_read_installed_plugins` (`_user_loader.py:307`) does **strict equality** (`_normalize_path(Path(projectPath)) != project_resolved`). Subdirectories of the plugin's projectPath are filtered out → plugin agents never enter `merged_pack` → `agent_def_resolver` returns None → `_configs[teammate_id]` is None → dashboard panel empty.

This very likely diverges from Claude Code's own behavior — Claude Code treats a project-scope plugin install as owning the whole project tree, not just the exact root, the same way `.git` discovery walks upward. claude-crew's filter is more restrictive than the host.

### Confirmed root cause

**`_read_installed_plugins` filters local-scope installs by exact `projectPath` equality, when it should accept any cwd at-or-below that path.** When claude-crew runs in a subdirectory of the plugin's projectPath, the plugin is silently dropped. The empty config panel is a downstream symptom — the load-time silence is the actual bug.

H1 (synthetic-AgentDef divergence) and H2 (namespace mismatch) remain ruled out for this incident. H1 is still a real code asymmetry worth closing as a small separate fix.

---

## Phase 2 — Synthesis

### Fix scope

**Two changes, one feature:**

1. **Loosen the projectPath filter** in `_read_installed_plugins`. Replace exact-equality with prefix containment: include the install when `project_resolved.is_relative_to(install_projectPath)` (the cwd is at-or-below the plugin's projectPath). Mirrors Claude Code's own project-scope semantics.

2. **Surface project-scope filter misses as a startup diagnostic** (via #25's `BrokerSnapshot.startup_diagnostics` channel). When a `scope: "local"` install is rejected for path mismatch, emit an INFO/WARN naming the plugin, its projectPath, and the current cwd — so operators see "plugin X scoped to /a/b, current cwd /c/d, not loaded" instead of a silent empty panel. New diagnostic category: `plugin_scope_miss`.

### Out of scope (split to follow-up)

- **H1 — synthetic-AgentDef vs `_resolve_agent_def` divergence.** Real bug, code-read finding (not this incident). File separately as a small fix.

### Acceptance tests

- AT1 — Plugin install with `projectPath: /a/b` is loaded when claude-crew runs in `/a/b/c` (subdirectory).
- AT2 — Plugin install with `projectPath: /a/b` is **not** loaded when claude-crew runs in `/a/x` (sibling).
- AT3 — Filter miss emits a `plugin_scope_miss` startup diagnostic visible on the dashboard's Startup Notices panel.
- AT4 — Existing exact-match path still works (regression coverage).
- AT5 — Plugin agent spawned in subdirectory case has populated config on dashboard (closes the original symptom).

### Open questions

1. **Symlinks and `..` segments** — `_normalize_path` already resolves these. `is_relative_to` operates on resolved paths. Should be fine, but call out in the test plan.
2. **`projectPath` typed as a glob/list ever?** Today it's `str`. Confirm the manifest schema before assuming.
3. Should the diagnostic fire even for installs that *do* load (informational)? Probably not — only fire on the miss case to avoid noise.
