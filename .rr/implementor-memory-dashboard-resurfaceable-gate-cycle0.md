# Implementor Memory: dashboard-resurfaceable-gate cycle 0

**Status:** PASS — all 5 slice tests green, full suite green (1445 passed, 34 skipped, 1 xfailed; only pre-existing `test_shutdown_signals` flake failed as documented in spec).

## What Was Done

### `claude_crew/ui/dashboard.html`
1. **MCTopBar** — added `pendingGateCount` and `onOpenGateTray` props; added a `data-testid="pending-gate-pill"` button rendered only when `pendingGateCount > 0` (amber badge showing count).
2. **ShapeGatePanel** — converted from self-managing auto-open (M0 dismissed-state pattern) to controlled component accepting `open` and `onClose` props. Removed `dismissed`/`lastKey` state. Renders only when `open && pending.length > 0`.
3. **InstanceStrip** — added `data-testid={`instance-tab-${cli.id}`}` to each instance tab div to enable Playwright tab-click assertions.
4. **MissionControlLayout** — added `gateOpen` state, `pendingGateCount` (flatMap all instances), `onOpenGateTray`, `onCloseGate`; wired new props into MCTopBar and ShapeGatePanel.

### `tests/dashboard/test_shape_resurface.py` (new file)
Five `@pytest.mark.dashboard` Playwright tests:
- `test_badge_renders_with_pending_proposal` — pill appears with "Gate" text
- `test_open_modal_shows_dag` — pill click → modal with `.shape-proposal-diagram` (SVG or code)
- `test_badge_persists_after_modal_closed` — close modal → pill stays visible
- `test_persists_across_instance_switch` — two-instance setup (leader+follower via shared InstanceRegistry tmp dir), follower has proposal, switch to follower tab → badge persists
- `test_clears_on_resolve` — POST /shape-approval → badge disappears

### `tests/test_shape_render.py` (scope-creep fix)
Updated 3 of 4 existing AT13/AT14 tests to click `[data-testid="pending-gate-pill"]` before waiting for `.shape-gate-panel`. The M0 auto-open behavior is gone; tests were broken by the controlled-component refactor. Minimal fix: add pill-click step.

## Key Design Points
- Badge count = ALL instances flatMap pending proposals (cross-instance, not just active tab)
- `gateOpen` state lives in MissionControlLayout — independent of activeId, survives instance switches
- The pill is only rendered when `pendingGateCount > 0`; disappears when proposals resolve
- Two-instance test uses `CLAUDE_CREW_INSTANCE_REGISTRY_DIR` env override with real TCP servers
