# Feature: per-teammate model/backend routing

**Status:** Shipped 2026-05-25. Built via repo-react (`repo-react/per-teammate-model-routing`).

## What

A claude-crew lead can now route **individual teammates** through a different
model backend — e.g. a local model via `claude-code-router` (ccr) — while the
lead and other teammates continue to hit the Anthropic API. Previously routing
was all-or-nothing per crew process (SDK teammates inherit the parent
`os.environ`, so a process-level `ANTHROPIC_BASE_URL` routed *every* teammate).

Two new `spawn_teammate` arguments:

- `env: dict[str, str] | None` — a per-teammate environment-variable override.
  Threaded `server → broker → factory → SdkTeammate` and merged into the SDK
  subprocess env via `ClaudeAgentOptions(env=...)`.
- `local_backend: bool | dict | None` — a convenience preset. `True` expands to
  the canonical local-backend env bundle; a dict accepts optional `base_url` /
  `api_key` overrides. Rides on top of the generic `env` mechanism (named
  bundle, not a parallel code path).

## Why

Run cheap, parallelizable, or privacy-sensitive work (mechanical refactors, test
runs, bulk edits) on free local compute while keeping high-stakes reasoning on
Anthropic — in the *same* crew, without restarting the MCP server. Also enables
on-prem / air-gapped crews. Foundation validated separately: a local Gemma model
via llama.cpp + ccr drives Claude Code agentic sessions.

## How

| Layer | File | Behavior |
|---|---|---|
| MCP tool | `server.py::spawn_teammate` | Validates `env` shape (non-string values + empty keys → `ToolError`); expands `local_backend` preset; merges `{**preset, **(env or {})}` (explicit env wins); forwards to broker |
| Broker | `broker.py::Broker.spawn_teammate` | Pure conditional pass-through: `if env is not None: factory_kwargs["env"] = env` |
| Factory | `factories.py` (`sdk_factory`, `stub_factory`, `default_factory` closure) | Same conditional pass-through; stub ignores `env` (signature uniformity) |
| Teammate | `sdk_teammate.py::SdkTeammate` | Stores `self._env` (value-type validated); `_build_merged_env()` returns `{**CREW_DEFAULTS, **caller}` with a per-key WARN when a caller overrides a crew default |
| Preset | `local_backend.py::local_backend_env()` | Returns `{ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY, CLAUDE_CODE_ATTRIBUTION_HEADER="0"}` |

### Key design decisions

- **Caller env wins over crew defaults**, with a WARN for visibility (the two
  crew defaults — `CLAUDE_CREW_UI_PORT=0`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` —
  are load-bearing).
- **`env=None` is byte-identical to pre-feature behavior** — the non-regression
  contract. Teammates spawned without an override are unchanged.
- **Conditional forwarding** at broker + factory — passing `env=None`
  unconditionally would break factories whose signature doesn't declare `env`
  (caught as a regression during the build and fixed).
- **`CLAUDE_CODE_ATTRIBUTION_HEADER=0` is baked into the preset** — without it,
  Claude Code injects a rotating telemetry hash at the front of the system
  prompt that busts local prompt caching (the gotcha that makes local routing
  actually usable).

## Usage

```python
# Route a teammate to the local backend (ccr default endpoint):
spawn_teammate(role="builder", local_backend=True)

# Custom local endpoint:
spawn_teammate(role="builder", local_backend={"base_url": "http://192.168.1.5:3456"})

# Raw env override:
spawn_teammate(role="builder", env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:3456",
                                    "ANTHROPIC_API_KEY": "sk-x",
                                    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"})

# Preset + explicit override (explicit wins):
spawn_teammate(role="builder", local_backend=True, env={"ANTHROPIC_API_KEY": "sk-real"})
```

Operationally the crew process needs the local backend (ccr + model server) up;
see `doc/ideas/local-model-backed-teammates.md` for the full recipe and gotchas.

## Tests

10 acceptance tests across `tests/test_local_backend.py`,
`tests/test_per_teammate_env.py`, `tests/test_factories.py`, `tests/test_broker.py`
— all stub-mode (no live model). Full suite green (1218 passed). Live SDK suite
exercised separately to confirm the env-merge doesn't break real subprocess
spawning (the env-less path is unchanged).

## Out of scope / follow-ups

- Mixed-routing UI (dashboard surfacing which teammate uses which backend).
- ccr config management / multiple concurrent local servers.
- Per-teammate model-*name* routing via ccr (model string is cosmetic under ccr).
- Validation key-shape check lives only at the MCP boundary; broker/factory/SDK
  trust the dict. A future direct (non-MCP) caller owns its own validation — a
  docstring note on `Broker.spawn_teammate(env=...)` would make this explicit.
