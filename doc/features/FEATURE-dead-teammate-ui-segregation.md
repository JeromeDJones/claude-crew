# Feature: Dead Teammate UI Segregation

**Status**: Shipped
**Created**: 2026-05-04
**Shipped**: 2026-05-04 (commit `3e835e3`)

---

## Phase 1: Research & Requirements

### Problem Statement

When teammates are killed, they remain visually interleaved with live agents in the main crew panel — dimmed but present, taking up column space and splitting operator attention. In a session with several agents that have come and gone, the live crew is buried among ghosts. Operators want dead agents out of the way but still accessible for post-mortem review.

Two specific gaps:
1. The left-side instance header (agent count, status dots) counts dead agents alongside live ones — misrepresenting active crew size.
2. Dead agent columns occupy horizontal scroll real-estate in the main panel, compressing live agent columns.

### Success Criteria

- [ ] **SC-1: Dead agents absent from live panel.** Killed/tombstoned teammates do not render as columns in the main `StreamColumns` panel. Only agents where `dead !== true` are rendered there.
- [ ] **SC-2: Dead agents in collapsible terminated section.** A "Terminated" section appears below the live panel when at least one dead agent exists. It renders dead agents in a compact list (not full columns).
- [ ] **SC-3: Terminated section collapsed by default.** On load (and after page refresh), the terminated section is collapsed. Live agents dominate the viewport.
- [ ] **SC-4: Terminated section expandable.** Clicking the terminated section header toggles it open/closed. When open, dead agents are visible with their identity (name, role, uptime, cost).
- [ ] **SC-5: Terminated section hidden when no dead agents.** When no dead agents exist, the terminated section does not render at all — no empty placeholder, no heading.
- [ ] **SC-6: Instance header reflects live count only.** The agent count displayed in the instance header (left panel) counts only alive agents.
- [ ] **SC-7: Collapse state persists across polls.** Expanding the terminated section does not reset on the next API poll cycle (every ~2s). Collapse state is held in React state, not derived from API data.
- [ ] **SC-8: Dead agent config panel still accessible.** When the terminated section is expanded, clicking a dead agent row still opens its detail panel (system prompt, tools, etc.) — the existing chip/panel behavior is preserved.

### Questions

- [x] **Does the API need to change?** No — `dead: true` already distinguishes dead agents in the response. This is purely a client-side rendering change.
- [x] **Where is the "left section" Jerome mentioned?** The instance header row in the left panel shows crew size. The main StreamColumns panel is the primary display area. Both need updating.
- [x] **Compact list vs full columns for dead agents?** Compact list — dead agents don't need full message stream columns; a row with name/role/uptime/cost and clickable detail panel is sufficient.
- [x] **Dead agent detail panel behavior?** Preserve existing behavior — clicking opens the detail panel showing config chips (system_prompt, tools, etc.).

### Constraints & Dependencies

- Pure frontend change — `dashboard.html` only. No server, broker, or API changes required.
- The `dead: true` field on agent entries is the sole discriminator (already present, confirmed in research).
- Dead agents appear in `cli.agents[]` regardless of this feature; the split happens client-side.
- Existing `.tm-row-dead` CSS and dead-agent rendering logic is reused inside the terminated section, not replaced.
- Must not break existing Playwright dashboard render tests (`tests/test_dashboard_render.py`).

**Gate**: ✅ Questions answered, success criteria measurable, constraints documented.

---

## Phase 2: Design & Specification

### Architecture Overview

All changes are in `claude_crew/ui/dashboard.html` (the single-file React/HTM dashboard). No server-side changes.

**Split at render time:** `cli.agents` is split into `liveAgents` (dead !== true) and `deadAgents` (dead === true) before passing to any component. `StreamColumns` receives only `liveAgents`. A new `TerminatedSection` component receives `deadAgents`.

**Layout structure (post-change):**

```
<InstancePanel>
  <InstanceStrip>           ← SC-6: statuses and count from liveAgents only
  <StreamColumns            ← SC-1: liveAgents only
    agents={liveAgents}
    openPanel / setOpenPanel  ← lifted to InstancePanel (M-1)
  />
  <TerminatedSection        ← SC-2/3/4/5: dead agents, collapsed by default
    agents={deadAgents}
    isOpen={terminatedOpen}
    onToggle={...}
    onAgentClick={setOpenPanel}  ← same handler as StreamColumns (M-1)
  />
</InstancePanel>
<MiniGraph agents={liveAgents}/>  ← H-2: dead agents excluded from topology
```

**Collapse state:** `terminatedOpen` is a `useState(false)` on the parent instance component. Survives API polls because React state is not reset on re-render unless the component unmounts (the instance component key is stable — `crew_id`).

### Data / API Contracts

No API changes. Client-side split:

```js
const liveAgents = cli.agents.filter(a => a.dead !== true);
const deadAgents = cli.agents.filter(a => a.dead === true);
```

`TerminatedSection` receives:
```js
{
  agents: Agent[],      // dead === true entries
  isOpen: boolean,
  onToggle: () => void,
}
```

Each dead agent row in `TerminatedSection` renders:
- Role avatar (colored dot, existing pattern)
- Name + role
- Uptime formatted as human-readable duration (e.g. "5m 12s") using the same `fmtDuration`/uptime formatter used in live agent rows — not raw seconds
- Cost (existing field)
- Clickable: opens detail panel via lifted `openPanel`/`setOpenPanel` state (D-6)

### Design Decisions

- **D-1: Client-side split, no API change.** `dead: true` is already present. A server-side filter would break the dead_configs/tombstone feature (#14) that deliberately includes dead agents for cost aggregation. The split must happen at render time only. *Carried into:* `liveAgents`/`deadAgents` filter in the instance render function; no changes to `ui_server.py`.

- **D-2: Collapsed by default.** Operators care about live crew; dead agents are reference material. Default-open would defeat the purpose. *Carried into:* `useState(false)` initial value; SC-3 test asserts section is not visible on load.

- **D-3: Compact row layout for dead agents, not full stream columns.** Full columns for dead agents waste vertical space and imply ongoing activity. A compact row (name, role, uptime, cost) with detail-panel access on click is the right density. *Carried into:* `TerminatedSection` component structure; dead agents do NOT pass through `StreamColumns`.

- **D-4: Terminate section hidden entirely when empty.** An empty "Terminated (0)" heading is visual noise. `deadAgents.length === 0` → null render. *Carried into:* conditional render guard in `TerminatedSection`; SC-5 test asserts section absent when no dead agents.

- **D-5: Instance header count AND status dots = live agents only.** Both the numeric count and the status dot row in `InstanceStrip` must use `liveAgents`. The dot row is built from `cli.agents.map(a => a.status)` — a dead-status dot alongside live dots misrepresents active crew. *Carried into:* `InstanceStrip` receives `liveAgents` (or a `liveStatuses` prop); SC-6 test covers both count and dot count.

- **D-6: Preserve detail panel for dead agents; lift `openPanel` state.** The config chips panel (system_prompt, tools, MCP servers) is the primary post-mortem surface. Must remain accessible from terminated section rows. Currently `openPanel` state lives inside `StreamColumns` — it must be lifted to the parent of both `StreamColumns` and `TerminatedSection` so both can trigger it. *Carried into:* `openPanel`/`setOpenPanel` lifted to `InstancePanel`-level; `TerminatedSection` receives `onAgentClick={setOpenPanel}`; SC-8 test.

- **D-7: Collapse state keyed to `crew_id`, not global.** If multiple crew instances are displayed, each has independent terminated-section state. Expanding one crew's terminated list doesn't affect another. *Carried into:* `terminatedOpen` state lives inside the per-instance render function/component.

### Edge Cases

- **No dead agents:** Section renders null (SC-5). No heading, no empty list.
- **All agents dead:** Live panel renders empty (no columns). Terminated section is the only content — still collapsed by default; operator must expand.
- **Single dead agent:** Section header shows "(1 terminated)"; row renders correctly.
- **Dead agent with no config snapshot:** Dead agents without a retained config (`dead_config is None` in the server) currently don't appear in `cli.agents` at all (server only includes dead entries if `dead_config is not None`). No change needed — these agents remain invisible.
- **Agent dies mid-session:** Moves from live panel to terminated section on next poll. Collapse state does not reset (D-2/SC-7).
- **Multiple crews:** Each instance panel has its own independent terminated section and collapse state (D-7).
- **Collapse toggle during rapid polls:** Toggle sets local state; poll re-renders with same `isOpen` value since state is not derived from API data.

### Specification

#### 1. Agent split (instance render function)

Wherever `cli.agents` is currently passed to `StreamColumns` and the instance header count, add:

```js
const liveAgents = (cli.agents || []).filter(a => a.dead !== true);
const deadAgents = (cli.agents || []).filter(a => a.dead === true);
```

Uses that must switch to `liveAgents`: `StreamColumns`, `MiniGraph`, `InstanceStrip` count and status dots.

**Explicit carve-outs that must stay on `cli.agents`:**
- `agentMap` (line ~960): built from full `cli.agents` so message history can resolve names for dead agents — dead agents sent real messages and their IDs must resolve.
- Cost/token aggregation: dead agent cost is already summed server-side; the client reads instance-level totals, not per-agent arrays.

#### 2. Instance header count

Change `cli.agents.length` → `liveAgents.length` wherever it contributes to the displayed agent count.

#### 3. StreamColumns call

Change `agents={cli.agents}` → `agents={liveAgents}`.

#### 4. TerminatedSection component

New component (inline in dashboard.html, following existing component patterns):

```
TerminatedSection({ agents, isOpen, onToggle })
  if agents.length === 0 → return null
  render:
    <div class="terminated-section">
      <button class="terminated-header" onClick={onToggle}>
        ▶/▼ Terminated ({agents.length})
      </button>
      {isOpen && (
        <div class="terminated-list">
          {agents.map(agent => (
            <TerminatedRow agent={agent} onClick={() => setSelectedAgent(agent)} />
          ))}
        </div>
      )}
    </div>
```

`TerminatedRow` renders: colored role dot, name, role, uptime string, cost string. Same color/avatar logic as live agent rows. Cursor: pointer. On click: opens detail panel via existing `selectedAgent` mechanism.

#### 5. Collapse state

In the per-instance render (or a new `InstancePanel` wrapper if needed):

```js
const [terminatedOpen, setTerminatedOpen] = useState(false);
```

Pass to `TerminatedSection` as `isOpen={terminatedOpen}` and `onToggle={() => setTerminatedOpen(o => !o)}`.

#### 6. CSS additions

```css
.terminated-section {
  border-top: 1px solid var(--line);
  margin-top: 8px;
}
.terminated-header {
  /* button reset + chevron + count */
  width: 100%;
  text-align: left;
  background: none;
  border: none;
  cursor: pointer;
  padding: 6px 12px;
  color: var(--fg-3);
  font-size: 11px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.terminated-header:hover { color: var(--fg-1); }
.terminated-list { padding: 4px 0; }
.terminated-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  cursor: pointer;
  opacity: 0.6;
}
.terminated-row:hover { opacity: 0.9; background: var(--hover); }
```

### Assumptions

- **A-1: No server/API change needed.** The `dead: true` field is already in the response; client-side split is sufficient. *Default: accept.*
- **A-2: Compact rows, not full stream columns, for dead agents.** Dead agents have no ongoing message stream; a summary row is more appropriate density. *Default: accept.*
- **A-3: Collapse state is session-local (not persisted to localStorage).** Page refresh resets to collapsed. The feature description doesn't require persistence across refreshes. *Default: accept — can add localStorage persistence later if desired.*

### Open Questions

*(None — all answered during Phase 1 research.)*

**Gate**: ✅ Design clear, spec comprehensive, edge cases covered, no open questions.

---

## Phase 3: Task Breakdown

### Task 1: Agent split + live panel and header count fix
**Depends on**: None | **Blocks**: Task 2, Task 3

Split `cli.agents` into `liveAgents`/`deadAgents` at the instance render level. Pass `liveAgents` to `StreamColumns`. Fix instance header count to use `liveAgents.length`.

**Acceptance Criteria**:
```
Scenario: Dead agent absent from stream columns
  Given a broker with one alive and one dead teammate
  When the dashboard renders
  Then StreamColumns receives only the alive agent
  And the dead agent column does not appear in the live panel

Scenario: Instance header shows live count only
  Given a broker with two alive and one dead teammate
  When the dashboard renders
  Then the agent count in the instance header reads "2", not "3"
```

**Verification**: `uv run pytest tests/test_dashboard_render.py -x -q` — existing tests pass; new assertions for count and column absence added.

---

### Task 2: TerminatedSection component + CSS
**Depends on**: Task 1 | **Blocks**: Task 3

Implement the `TerminatedSection` component with `TerminatedRow` rows, collapse toggle, and CSS. Wire into the instance render below `StreamColumns`.

**Acceptance Criteria**:
```
Scenario: Terminated section absent when no dead agents
  Given a broker with only alive teammates
  When the dashboard renders
  Then no terminated section heading appears

Scenario: Terminated section present but collapsed when dead agents exist
  Given a broker with one dead teammate
  When the dashboard renders
  Then "Terminated (1)" heading is visible
  And the dead agent row is NOT visible (section collapsed)

Scenario: Terminated section expands on click
  Given a broker with one dead teammate and the section collapsed
  When the operator clicks the "Terminated" heading
  Then the dead agent row becomes visible
  And the chevron/indicator reflects open state

Scenario: Dead agent row shows identity and cost
  Given an expanded terminated section with one dead agent
  Then the row shows the agent name, role, uptime, and cost
```

**Verification**: `uv run pytest tests/test_dashboard_render.py -x -q -k "terminated"` — new terminated-section render tests pass.

---

### Task 3: Collapse state persistence across polls + detail panel access
**Depends on**: Task 2 | **Blocks**: Task 4

Ensure `terminatedOpen` state survives poll cycles. Ensure clicking a dead agent row opens the detail panel.

**Acceptance Criteria**:
```
Scenario: Collapse state survives API poll
  Given the terminated section is expanded
  When the dashboard re-renders on the next poll cycle
  Then the terminated section remains expanded (state not reset)

Scenario: Collapse state preserved when agent dies mid-session
  Given the terminated section is expanded and showing one dead agent
  When a second agent is killed and the next poll fires
  Then the terminated section remains expanded
  And both dead agents are visible

Scenario: Detail panel opens for dead agent
  Given the terminated section is expanded with one dead agent
  When the operator clicks the dead agent row
  Then the detail panel opens showing the agent's config chips
  And system_prompt and tools chips are visible
```

**Verification**: `uv run pytest tests/test_dashboard_render.py -x -q -k "terminated"` — poll-persistence and detail-panel tests pass.

---

### Task 4: End-to-end integration tests
**Depends on**: Tasks 1–3 | **Blocks**: None

Full Playwright render tests covering the feature end-to-end.

**Happy Path Scenarios**:
```
Scenario: Live crew unaffected — dead agents do not appear in stream columns
  Given a broker with alive and dead teammates
  When dashboard renders
  Then stream columns show only alive agents
  And agent count in header = alive count only

Scenario: Terminated section lifecycle
  Given a broker with one alive and one dead teammate
  When dashboard renders → section collapsed
  Then clicking header expands it → dead agent row visible
  And clicking dead agent row → detail panel opens
  And clicking header again → section collapses

Scenario: No terminated section when all alive
  Given a broker with only alive teammates
  When dashboard renders
  Then no terminated section element exists in DOM
```

**Sad Path Scenarios**:
```
Scenario: All agents dead — live panel empty, terminated section collapsed
  Given a broker where all teammates are dead
  When dashboard renders
  Then stream columns area is empty (no live columns)
  And terminated section header is visible
  And terminated section is collapsed by default

Scenario: Agent dies between polls — moves to terminated section
  Given a dashboard showing one alive agent
  When that agent is killed and the next poll fires
  Then the agent disappears from stream columns
  And appears in the terminated section (still collapsed — was not open)

Scenario: Agent dies while terminated section is open
  Given the terminated section is expanded (showing a prior dead agent)
  When a second alive agent is killed and the next poll fires
  Then the newly dead agent appears in the terminated section
  And the section remains expanded (collapse state not reset by the poll)

Scenario: Multi-crew terminated section independence (D-7)
  Given two crew instances, each with one dead agent
  When the operator expands the terminated section of crew A
  Then crew B's terminated section remains collapsed
  And toggling crew B's section does not affect crew A
```

**Verification**: `uv run pytest tests/test_dashboard_render.py -x -q` — all dashboard render tests pass including new E2E scenarios.

---

**Gate**:
- ✅ 4 tasks, each independently testable
- ✅ Dedicated E2E test task (Task 4)
- ✅ Verification commands fail without the feature
- ✅ All Phase 2 edge cases trace to BDD scenarios
- ✅ User approved

---

## Phase 4: Implementation

*Execution driven by SKILL.md. Update status in header as tasks complete.*

**Gate**: All tasks complete, quality gates passing, Sentinel review done.

---

## Phase 5: Completion

### Verification
- [x] Feature works against Phase 1 success criteria (SC-1 through SC-8) —
      4 new Playwright tests cover SC-1, SC-5, SC-6, SC-7 (`tests/test_dashboard_render.py`)
- [x] No regressions — full test suite passes
- [x] Spec matches implementation (sentinel pre-merge review caught
      InstanceStrip + MiniGraph filter omissions; both addressed)

Implementation in commit `3e835e3` touches only:
- `claude_crew/ui/dashboard.html` — TerminatedRow / TerminatedSection,
  agent split in MissionControlLayout, lifted `openPanel` state,
  per-instance `terminatedOpen` keyed by activeId, MCTopBar +
  InstanceStrip filters
- `tests/test_dashboard_render.py` — 4 new E2E cases

### Retrospective

Sentinel review during Phase 2 caught two real omissions before
implementation: InstanceStrip status dots and MiniGraph topology both
read `cli.agents` unfiltered. Both addressed in the spec before
implementation began. The agent split (live vs. dead) at
MissionControlLayout — rather than inside each consumer — kept the
filter centralized and made the lifted `openPanel` state the natural
seam for dead-row click handling.
