# Feature: per-teammate backend routing

**Status:** Shipped 2026-05-25; reframed 2026-05-29 around Bedrock + custom
endpoints. Originally built via repo-react (`repo-react/per-teammate-model-routing`).

## What

A claude-crew lead can route **individual teammates** to a different backend than
the lead's own — e.g. Amazon Bedrock for one teammate, a custom gateway for
another, default Anthropic API for the rest. Previously routing was
all-or-nothing per crew process (SDK teammates inherit the parent `os.environ`,
so a process-level `ANTHROPIC_BASE_URL` or `CLAUDE_CODE_USE_BEDROCK` routed
*every* teammate).

Two new `spawn_teammate` arguments:

- `env: dict[str, str] | None` — a per-teammate environment-variable override.
  Threaded `server → broker → factory → SdkTeammate` and merged into the SDK
  subprocess env via `ClaudeAgentOptions(env=...)`. The general escape hatch
  for any backend-routing env shape the SDK supports.
- `custom_endpoint: dict | None` — a convenience preset for an Anthropic-shape
  endpoint behind a custom base URL (gateway, proxy, self-hosted router).
  Dict carries required `base_url` and optional `api_key`. Rides on top of the
  generic `env` mechanism (named bundle, not a parallel code path).

For Amazon Bedrock, callers pass `claude_crew.backend_routing.bedrock_env(...)`
through the generic `env=` parameter — Bedrock uses a different env shape
(`CLAUDE_CODE_USE_BEDROCK=1` + AWS credential resolution), so it gets its own
preset rather than the `custom_endpoint` form.

## Why

Per-teammate routing decouples cost / capability / compliance choices from crew
topology. Run high-stakes reasoning on Anthropic, scale-out cheap parallel work
through Bedrock provisioned throughput, route compliance-sensitive workloads
through a privately hosted gateway — all in the same crew, without restarting
the MCP server. Also unlocks on-prem and air-gapped crews where the gateway
controls egress.

## How

| Layer | File | Behavior |
|---|---|---|
| MCP tool | `server.py::spawn_teammate` | Validates `env` shape (non-string values + empty keys → `ToolError`); expands the `custom_endpoint` preset; merges `{**preset, **(env or {})}` (explicit env wins); forwards to broker |
| Broker | `broker.py::Broker.spawn_teammate` | Pure conditional pass-through: `if env is not None: factory_kwargs["env"] = env` |
| Factory | `factories.py` (`sdk_factory`, `stub_factory`, `default_factory` closure) | Same conditional pass-through; stub ignores `env` (signature uniformity) |
| Teammate | `sdk_teammate.py::SdkTeammate` | Stores `self._env` (value-type validated); `_build_merged_env()` returns `{**CREW_DEFAULTS, **caller}` with a per-key WARN when a caller overrides a crew default |
| Presets | `backend_routing.py::bedrock_env()`, `custom_endpoint_env()` | Canonical env dicts for the two named backend cases |

### Key design decisions

- **Caller env wins over crew defaults**, with a WARN for visibility (the crew
  defaults — `CLAUDE_CREW_UI_PORT=0`, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
  `DISABLE_TELEMETRY=1` — are load-bearing).
- **`env=None` is byte-identical to pre-feature behavior** — the non-regression
  contract. Teammates spawned without an override are unchanged.
- **Anthropic-shape responses are assumed end-to-end.** Backends that produce a
  different wire shape (e.g. OpenAI-style or other non-Anthropic shapes) MUST translate
  upstream — claude-crew's token / cost attribution path is shape-uniform with
  no dual-shape branching.
- **Conditional forwarding** at broker + factory — passing `env=None`
  unconditionally would break factories whose signature doesn't declare `env`
  (caught as a regression during the build and fixed).
- **`CLAUDE_CODE_ATTRIBUTION_HEADER=0` is baked into the custom-endpoint preset**
  — without it, Claude Code injects a rotating telemetry hash at the front of
  the system prompt that busts prompt caching on the upstream side. Bedrock
  preset omits this (Bedrock attribution is handled differently).

## Usage

```python
# Route a teammate to Amazon Bedrock (credentials picked up from env / role):
from claude_crew.backend_routing import bedrock_env
spawn_teammate(role="builder", env=bedrock_env(aws_region="us-east-1"))

# Route a teammate through a custom Anthropic-shape gateway:
spawn_teammate(
    role="builder",
    custom_endpoint={"base_url": "http://gateway.internal:9999", "api_key": "sk-real"},
)

# Raw env override (any SDK-supported routing shape):
spawn_teammate(role="builder", env={"ANTHROPIC_BASE_URL": "http://proxy:3456",
                                    "ANTHROPIC_API_KEY": "sk-x",
                                    "CLAUDE_CODE_ATTRIBUTION_HEADER": "0"})

# Preset + explicit env (explicit wins):
spawn_teammate(
    role="builder",
    custom_endpoint={"base_url": "http://proxy:3456"},
    env={"ANTHROPIC_API_KEY": "sk-real"},
)
```

## Tests

Acceptance tests across `tests/test_backend_routing.py`,
`tests/test_per_teammate_env.py`, `tests/test_factories.py`, `tests/test_broker.py`
— all stub-mode (no live model). Full suite green. Live SDK suite exercised
separately to confirm the env-merge doesn't break real subprocess spawning (the
env-less path is unchanged).

## Out of scope / follow-ups

- Mixed-routing UI (dashboard surfacing which teammate uses which backend).
- Validation key-shape check lives only at the MCP boundary; broker / factory /
  SDK trust the dict. A future direct (non-MCP) caller owns its own validation
  — a docstring note on `Broker.spawn_teammate(env=...)` would make this explicit.
