# Active Model Truthful Display

## Why

Operators today see `agent.model` on the dashboard — a value derived from the spawn-time argument, not from what the API actually used. During a debugging session we spent ~30 min chasing a phantom "override doesn't work" bug because:

- Teammates self-report wrong model strings (haiku, sonnet-4-5) regardless of what was set
- Dashboard `config.model` reads the pack-declared model, not the override
- Cost telemetry can mislead (pricing constants / cache amortization)

The only authoritative signal is `AssistantMessage.model`, populated directly from the Anthropic API response (`message_parser.py:148-150`). This plan surfaces that value to the UI so the question "what model is this teammate actually using?" is answered by glance, not by forensics.

## Scope

In scope:
1. Capture `AssistantMessage.model` per turn on SdkTeammate as `self._last_assistant_model`
2. Surface on `status_snapshot()` → broker snapshot → `/api/state` agent record as `active_model`
3. Preserve at tombstone (mirror `last_turn_input_tokens` plumbing)
4. Dashboard chip: use `active_model` when present; fall back to `agent.model` (configured value) muted/italic while no turn has run
5. Tools/config popup: when pack default, spawn override, and active model are not all the same, show the chain (`pack → override → active`); else collapse to single "active" line

Out of scope:
- Fix `total_cost_usd` accuracy (separate concern; file in BACKLOG)
- Detect / surface mid-session model swaps with special UI (always-latest is enough)
- Stale broker-process leak from `/mcp` reconnect (separate cleanup)

## Architecture

Mirror the `last_turn_input_tokens` pattern exactly:

```
AssistantMessage.model
        ↓
SdkTeammate._last_assistant_model            (new)
        ↓
SdkTeammate.status_snapshot()["active_model"]  (new key)
        ↓
broker snapshot: live = StatusSnapshot.status["active_model"]
broker snapshot: dead = TeammateInfo.active_model_at_death  (new field)
        ↓
ui_server: agent_entry["active_model"] = ...
        ↓
dashboard.html ModelBadge({ model: agent.active_model || agent.model })
                ConfigDetailPanel chain block
```

### Field semantics

- `agent.model` — *intent*. The configured value (spawn override OR pack default, normalized to family alias for the badge). Unchanged.
- `agent.active_model` — *observed*. The exact model id returned by the API on the last AssistantMessage. `null` until the first assistant message arrives.
- `config.model` — unchanged. The pack's AgentDefinition.model. Used for the "pack" line in the chain when active diverges.

The reason for keeping `agent.model` as configured: it's the chip default when no turn has fired yet, and it's the comparison anchor for the chain block.

## File-by-file

### `claude_crew/sdk_teammate.py`
- Add `self._last_assistant_model: str | None = None` next to other token fields (~line 513)
- In the AssistantMessage handler (~line 287), capture `msg.model`:
  ```python
  if isinstance(msg, AssistantMessage):
      model_str = getattr(msg, "model", None)
      if isinstance(model_str, str) and model_str:
          self._last_assistant_model = model_str
      ...
  ```
  Note: `_collect_response_text` runs on the drain loop and currently has no `self` access — capture needs to happen at the call site (`_handle_one_turn` or wherever AssistantMessages are observed by the teammate). If `_collect_response_text` is the only consumer, return it from `TurnDrainResult` (new optional field) and assign in `_handle_one_turn` like the token fields are. Check live code to pick the right pattern.
- In `status_snapshot()` (~line 956): `snap["active_model"] = self._last_assistant_model`

### `claude_crew/teammate.py`
- Base `status_snapshot()` (~line 216) — add `"active_model": None` so stub/test teammates have a stable shape

### `claude_crew/broker.py`
- `TeammateInfo`: add `active_model_at_death: str | None = None` (next to `last_turn_input_tokens_at_death` at line 67)
- `_tombstone_teammate` (~line 404): capture `active_model_at_death = snap.get("active_model")` (or None on the missing-snap branches)
- `snapshot()` dead-build path (~line 835): include `"active_model": info.active_model_at_death`
- Live-build path (~line 877): include `"active_model": snap.get("active_model")`

### `claude_crew/ui_server.py`
- Live branch (~line 217 agent_entry): add `"active_model": snap.get("active_model")`
- Dead branch (~line 267 dead_entry): add `"active_model": dead_config_active or None` — pull from snapshot tombstone field

### `claude_crew/ui/dashboard.html`
- `ModelBadge` (~line 458): use `active_model` when truthy, else fall back to `model`, render unobserved fallback with reduced opacity / italic
- `ConfigDetailPanel` (~line 578): replace the single `model` block with a chain renderer:
  - Resolve three values: `pack = config.model` (pack agent-def model), `override = agent.model_override` (spawn override — needs surfacing OR inferred as `agent.model when configured_model_explicit`), `active = agent.active_model`
  - Question: do we have spawn-override visibility? We have `agent.model` (configured, post-resolution) and `config.model` (pack default). If they differ, the difference IS the override. If equal, no override was given.
  - Render: when all three (pack vs configured vs active) collapse to one value, show single line. Otherwise show: `pack: X` → `override: Y` (omitted if no override) → `active: Z`.

### Tests

#### `tests/test_sdk_teammate.py` (or nearest equivalent)
- Unit: feed a synthetic AssistantMessage with `model="claude-opus-4-7"` through the handler; assert `teammate._last_assistant_model == "claude-opus-4-7"` and `status_snapshot()["active_model"] == "claude-opus-4-7"`
- Unit: status_snapshot before any AssistantMessage → `active_model is None`
- Unit: multiple AssistantMessages → latest wins

#### `tests/test_broker.py`
- Snapshot live: `active_model` field present and reflects teammate snap value
- Tombstone: `active_model_at_death` captured; dead-build path exposes `active_model`

#### `tests/test_ui_server.py`
- `/api/state` shape: live agent has `active_model` key
- Dead agent has `active_model` key

#### Live verification (manual)
- Spawn explorer with `model=claude-opus-4-7`, send a prompt, confirm dashboard chip shows `opus` and `/api/state` reports `active_model="claude-opus-4-7"`
- Spawn explorer with no override, confirm chip shows `haiku`
- Open tools popup on opus-override teammate, confirm chain renders `pack: haiku → override: opus → active: opus`

## Steps

1. ✅ Branch `feat/active-model-truthful-display`
2. SdkTeammate: capture + store + expose on snapshot
3. Base Teammate snapshot key
4. Broker: tombstone + snapshot live/dead
5. UI server: surface on `/api/state`
6. Dashboard: chip uses active_model
7. Dashboard: config panel chain renderer
8. Tests at each layer (BDD: write or update first where practical)
9. Run full suite (`uv run pytest`)
10. Live validation with spawned teammates
11. Spawn `sentinel` claude-crew teammate for review
12. Address review feedback → commit → push → PR

## Validation contract

- `uv run pytest` clean
- Live spawn shows opus override → chip renders "opus" — the bug we hit today no longer reproduces
- Chain renderer makes pack-vs-override-vs-active visible at a glance
- No regression on existing dashboard fields (cost, tokens, tools, status)
