# Spec: local-model-attribution

## Problem

When a teammate is spawned with `local_backend=True` (Anthropic → ccr → llama.cpp),
`get_teammate_status` and the dashboard always show `total_input_tokens=0`,
`total_output_tokens=0`, `total_cost_usd=0`, `last_turn_input_tokens=0`,
`last_turn_output_tokens=0`, and `last_turn_peak_invocation_input_tokens=0` —
even after the teammate has completed multiple turns and consumed real prompt /
completion tokens on the local server. Anthropic-backed teammates populate these
fields cleanly because `_collect_response_text` reads
`ResultMessage.usage["input_tokens"|"output_tokens"|"cache_*"]` (the
Anthropic-shaped keys); the local response path returns `usage` populated with
the OpenAI shape (`prompt_tokens`, `completion_tokens`,
`prompt_tokens_details.cached_tokens`), so every Anthropic-key lookup defaults to
`0` and no field is ever assigned. Operators can't see whether a local teammate
is making progress, can't compare turn costs, can't reason about local cache hit
rates, and can't trust any aggregate roll-up that mixes local and Anthropic
teammates. A second, same-surface gap surfaces under the same spawn-time
options-wiring block: the honor-pack-tools-allowlist contract is bypassed by
**plugin-provided** MCP servers (see Design Decisions → "Allowlist Completeness"),
which leaks `mcp__plugin_*` tools into teammates that explicitly opted out via
their pack `tools:` allowlist. The capability gap: per-turn input/output tokens
(and a deterministic cost figure) attributed to local-backend teammates AND
plugin-MCP suppression on every spawn, both surfaced through the existing
`status_snapshot()` / SDK-options pipeline so `get_teammate_status`, the
dashboard, and the tool catalog render correctly and consistently across
backends.

## Architecture Overview

The fix lives in **one seam** — `_extract_token_cost_from_rm` in
`claude_crew/sdk_teammate.py` (the inner helper inside `_collect_response_text`)
— plus a small carrier so the helper knows which key-shape to expect.

```
spawn_teammate(local_backend=True)
     │  (existing) sets ANTHROPIC_BASE_URL env on the teammate
     ▼
SdkTeammate(_env=..., is_local derived in broker snapshot)
     │
     ▼
_handle_one_turn  ─►  _collect_response_text(client, ..., is_local=<bool>)
                            │
                            ▼
                     _extract_token_cost_from_rm(rm, is_local)
                       ├─ Anthropic shape: input_tokens / output_tokens /
                       │  cache_read_input_tokens / cache_creation_input_tokens
                       └─ OpenAI shape:    prompt_tokens / completion_tokens /
                          prompt_tokens_details.cached_tokens
                            │
                            ▼
                  TurnDrainResult.turn_input_tokens / turn_output_tokens /
                  cumulative_cost_usd / peak_invocation_input_tokens
                            │
                            ▼
                  status_snapshot()  →  get_teammate_status / dashboard
```

The downstream pipeline (`Teammate._end_turn` accumulation, `status_snapshot()`,
broker `LiveTeammateInfo.is_local`, dashboard `agents[].tokens` /
`agents[].cost`) is **unchanged** — once the helper returns non-zero values, the
existing telemetry pipeline carries them end-to-end.

The same `_build_options`-style block in `sdk_teammate.py` that writes
`opts_kwargs["mcp_servers"]` (the deny-by-default allowlist contract) is also
where `--strict-mcp-config` is added via the SDK's `extra_args` pass-through —
co-located because both edits touch the same options-construction surface.

### Call-site survey

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| `_handle_one_turn` per-turn drain | `claude_crew/sdk_teammate.py` | `await _collect_response_text(client, stamp_activity, record_task_notif)` | The only production caller; needs the new `is_local` arg threaded in. |
| `_extract_token_cost_from_rm` peak-invocation read (AssistantMessage path) | `claude_crew/sdk_teammate.py` | Inline inside `_collect_response_text`, reads `msg.usage["input_tokens" + cache_*]` | Must also accept the OpenAI key shape (`prompt_tokens` + `cached_tokens`) when `is_local=True`. |
| Test fixtures emitting ResultMessage | `tests/fakes/sdk.py` (`text_response_with_usage`) | Currently emits Anthropic-shaped dict | New sibling helper `text_response_with_openai_usage` keeps the existing fixture untouched. |
| Options-construction block (`opts_kwargs["mcp_servers"]` write) | `claude_crew/sdk_teammate.py` | Sets the deny-by-default MCP allowlist; today does NOT set `extra_args` | Same surface — add `extra_args={"strict-mcp-config": None}` merge here. |

Resolution: **single helper, polymorphic on `is_local`**. The helper inspects
which key family is present and reads accordingly — no second helper, no
duplicate code path. The `is_local` arg is a hint for diagnostic logging only;
key-shape detection is the actual switch. The `extra_args` merge is a separate
one-line addition in the same options-wiring block.

## Data / API Contracts

```python
# claude_crew/sdk_teammate.py

async def _collect_response_text(
    client: Any,
    stamp_activity: Callable[[], None] | None = None,
    record_task_notif: Callable[[str, TaskNotificationMessage], None] | None = None,
    *,
    is_local: bool = False,        # NEW — defaults to False (Anthropic shape)
) -> TurnDrainResult: ...

def _extract_token_cost_from_rm(
    rm: ResultMessage,
    *,
    is_local: bool = False,        # NEW — hint for logging; key-shape detection is independent
) -> tuple[int | None, int | None, float | None]:
    """
    Anthropic key shape (existing behavior, unchanged):
        usage = {
          "input_tokens": int,
          "output_tokens": int,
          "cache_read_input_tokens": int,       # optional
          "cache_creation_input_tokens": int,   # optional
        }
        per_turn_input = input_tokens + cache_read_input_tokens + cache_creation_input_tokens
        per_turn_output = output_tokens

    OpenAI key shape (NEW, used by local backend via ccr → llama.cpp):
        usage = {
          "prompt_tokens": int,
          "completion_tokens": int,
          "prompt_tokens_details": {"cached_tokens": int},   # optional
        }
        per_turn_input  = prompt_tokens                       # prompt_tokens already includes cached
        per_turn_output = completion_tokens
        # Cache attribution exposed (see SC-3) but does NOT alter per_turn_input
        # to avoid double-counting (OpenAI's prompt_tokens already includes cached).

    Cost:
        cumulative_cost_usd reads ResultMessage.total_cost_usd UNCHANGED.
        For local backends the SDK passes 0.0 (or omits the field). Net effect:
        total_cost_usd stays at 0.0 for local teammates — honest, deterministic,
        documented (D-3).

    Detection:
        Anthropic-shaped if "input_tokens" present; OpenAI-shaped if
        "prompt_tokens" present; both absent → return (None, None, cost_only).
        Both present (defensive) → prefer Anthropic shape; log INFO.
    """

# Peak-invocation read inside _collect_response_text — AssistantMessage usage
# is checked with the same dual-shape detection so the cliff signal is non-zero
# for local teammates too.

# Options construction (same block that writes the deny-by-default mcp_servers).
# Add unconditional --strict-mcp-config via extra_args pass-through:
opts_kwargs.setdefault("extra_args", {})["strict-mcp-config"] = None
# Merge semantics: setdefault preserves any pre-existing extra_args; the
# strict-mcp-config key is set unconditionally (claude-crew always honors the
# allowlist contract). Value None means "flag with no value" → CLI gets bare
# --strict-mcp-config.
```

No new public API. `get_teammate_status` shape and `status_snapshot()` keys are
unchanged — only the numbers they carry change from zero to real values for
local teammates. The `extra_args` addition is internal to the SDK-options
construction and not surfaced to MCP tool callers.

## Design Decisions

- **Dual key-shape parser in one helper, polymorphic on detected shape** — *Rationale:* one parse site, no duplicated reduction logic, no risk of one path drifting from the other; the `is_local` flag is the operator-intent hint but the actual switch is the presence of `prompt_tokens` vs `input_tokens` keys, so a future backend that re-routes Anthropic-shape through local still works. — *Carried into:* `_extract_token_cost_from_rm` signature; AT#1, AT#2, AT#3.
- **OpenAI `prompt_tokens` is treated as the full per-turn input total (cached + uncached)** — *Rationale:* OpenAI/llama.cpp's `prompt_tokens` already includes the cache_hit portion (verified against llama.cpp's own accounting); adding `cached_tokens` again would double-count and inflate session totals by 2-3× on a warm cache. — *Carried into:* `_extract_token_cost_from_rm` OpenAI branch; AT#2.
- **Cost stays at $0.00 for local teammates this slice** — *Rationale:* zero is honest (no money changed hands); a synthetic GPU-amortization or energy-cost figure is a separate operator-preference call (deferred to a follow-up). The cost field is wired to whatever `ResultMessage.total_cost_usd` reports — if ccr/SDK ever populates it, the value flows through unchanged with no extra code. — *Carried into:* AT#5; Out of Scope #1.
- **`is_local` is threaded explicitly from `_handle_one_turn` to `_collect_response_text`** — *Rationale:* the teammate already knows its env shape (`self._env` carries `ANTHROPIC_BASE_URL` set by the `local_backend` preset); deriving locally would duplicate the broker's detection rule. Pass it explicitly so the helper is unit-testable without a broker. — *Carried into:* `_collect_response_text(is_local=...)` kwarg; AT#4.
- **Peak-invocation input also uses dual-shape detection** — *Rationale:* the cliff signal (`last_turn_peak_invocation_input_tokens`) is what feeds the dashboard's context-window bar's *Anthropic* path; for local teammates the dashboard already prefers `/slots`, but this field is still surfaced via `get_teammate_status` and zero values mislead operators reading the MCP output directly. — *Carried into:* AssistantMessage `usage` read in `_collect_response_text`; AT#3.
- **Detection ambiguity (both key families present) prefers Anthropic shape** — *Rationale:* this is the canonical SDK contract; a defensive fallback rather than a silent OR-merge avoids accidental double-count. Log an INFO line so operators can spot it if it ever occurs. — *Carried into:* `_extract_token_cost_from_rm` dual-key branch; AT#6.
- **Existing Anthropic-backed code path is untouched at the value level** — *Rationale:* zero risk of regressing the production telemetry pipeline. The `is_local=False` default keeps every existing call-site, fixture, and test passing without edits beyond the one new kwarg with a default. — *Carried into:* test_e2e_token_cost.py runs unchanged; AT#7.

### Allowlist Completeness

The honor-pack-tools-allowlist contract (commit c683922) makes pack `tools:`
the wire-level catalog AND pre-approval set, with MCP servers deny-by-default
for entries configured in `~/.claude.json`. That works for user-config MCP
servers but does **not** suppress **plugin-provided** MCP servers declared in
`~/.claude/plugins/cache/<plugin>/.claude-plugin/plugin.json`. Mechanism: when
a pack declares `skills:`,
`claude_agent_sdk._internal.transport.subprocess_cli._apply_skills_defaults`
auto-sets `setting_sources=["user","project"]`. With those setting sources,
the Claude CLI subprocess loads the user-level plugin registry and
auto-registers every enabled plugin's `mcpServers`. Transcript-confirmed leak:
this teammate (rr-planner) successfully called
`mcp__plugin_context-mode_context-mode__ctx_batch_execute` four times during
the planning turn — a tool that should have been denied by the pack allowlist.

**Fix:** the claude-agent-sdk's `ClaudeAgentOptions.extra_args: dict[str, str | None]`
is a generic CLI pass-through (`subprocess_cli.py` ~line 340). Setting
`extra_args={"strict-mcp-config": None}` adds the CLI flag
`--strict-mcp-config`, which makes the CLI ignore all MCP sources except the
explicit `--mcp-config` payload (which claude-crew already writes as
deny-by-default). Plugin-MCP suppression, no other behavior change. Added in
the same `opts_kwargs["mcp_servers"]` write block via `setdefault` to preserve
any pre-existing `extra_args` from other call-sites. *Carried into:*
`opts_kwargs["extra_args"]` merge in `sdk_teammate.py`; AT#9.

## Edge Cases

- `usage` dict absent (`None`) on the ResultMessage — returns `(None, None, cost_only)`; downstream accumulator skips assignment, last-good values are preserved.
- `usage` present but empty `{}` — both key families absent; treated same as absent.
- OpenAI shape with `prompt_tokens_details` missing — `cached_tokens` defaults to `0`; per_turn_input unaffected (it's already `prompt_tokens`).
- OpenAI shape with `prompt_tokens_details` present but `cached_tokens` non-int — log WARNING with keys (not values), treat as 0.
- OpenAI shape with `completion_tokens` non-int / negative / float — same WARNING-with-keys pattern as the existing Anthropic branch; per_turn_output becomes `None` for that turn (last-good preserved).
- Both `input_tokens` and `prompt_tokens` present on one usage dict — log INFO; prefer Anthropic shape; do not OR-merge.
- `ResultMessage.total_cost_usd` is `0.0` (typical local case) — flows through unchanged; cumulative cost stays `0.0`. This is correct, not a bug.
- Local-backend teammate spawned **without** `ANTHROPIC_BASE_URL` (raw `env={}` override) — `is_local=False` propagates; helper still detects OpenAI shape if ccr-style usage shows up; values still attribute. The hint is an optimization, not a gate.
- Anthropic-backed teammate gets an OpenAI-shape `usage` (impossible in practice, but defensive) — shape-detection wins over hint; values attribute correctly; INFO logged.
- Malformed cost (string, dict) — existing WARNING path covers it; cost stays at last-good.
- Mid-turn kill during a local turn — `_close_open_tools` runs; no `ResultMessage` arrives; per-turn deltas not assigned (existing behavior). Cumulative totals from prior turns preserved on the tombstone.
- Teammate dies before first turn produces a `ResultMessage` — `total_input_tokens_at_death == 0`, `last_turn_input_tokens_at_death == 0` (existing tombstone behavior, unchanged).
- Pre-existing `extra_args` on `opts_kwargs` (from a future caller or test override) — `setdefault({})` preserves the dict; `["strict-mcp-config"] = None` is set unconditionally (no other code path should clear it; if one does, that's an explicit policy override and not this slice's concern).
- A pack with no `skills:` and `setting_sources` unset — the SDK would not auto-load plugin MCP either way; `--strict-mcp-config` is a no-op (still safe to set).

## Acceptance Tests

1. **OpenAI-shape usage on a local-backend teammate is attributed end-to-end.** Given a fake `ClaudeSDKClient` patched into `sdk_teammate.ClaudeSDKClient` that returns a `ResultMessage` whose `usage = {"prompt_tokens": 1200, "completion_tokens": 350, "prompt_tokens_details": {"cached_tokens": 900}}` and `total_cost_usd = 0.0`, when the broker spawns the teammate with `env={"ANTHROPIC_BASE_URL": "http://127.0.0.1:3456", "ANTHROPIC_API_KEY": "sk-local"}` and drives one turn, then `broker.get_teammate_status(tid)["total_input_tokens"] == 1200`, `["total_output_tokens"] == 350`, `["last_turn_input_tokens"] == 1200`, `["last_turn_output_tokens"] == 350`, and `["total_cost_usd"] == 0.0`.

2. **No double-counting of cached prompt tokens.** Given the same fake as AT#1 driven for two turns (`prompt_tokens=1000, cached_tokens=800` then `prompt_tokens=1500, cached_tokens=1300`), when both turns complete, then `total_input_tokens == 2500` (1000 + 1500, NOT 2500 + 800 + 1300). Per-turn `last_turn_input_tokens == 1500` after turn 2.

3. **Peak-invocation input is attributed from AssistantMessage OpenAI usage.** Given a fake SDK that emits two `AssistantMessage` items with `usage = {"prompt_tokens": 800}` then `usage = {"prompt_tokens": 1500}` followed by a `ResultMessage`, when the turn drains, then `status_snapshot()["last_turn_peak_invocation_input_tokens"] == 1500`.

4. **`is_local` flag threads from `_handle_one_turn` to `_collect_response_text`.** Given an `SdkTeammate` constructed with `env={"ANTHROPIC_BASE_URL": "http://x"}`, when its `_handle_one_turn` invokes `_collect_response_text`, then the call is observed (via a monkeypatched spy capturing kwargs) with `is_local=True`. Given the same teammate constructed with `env=None`, the same call is observed with `is_local=False`.

5. **Cost flows through verbatim from `ResultMessage.total_cost_usd`.** Given a fake `ResultMessage` for a local teammate with `usage = {"prompt_tokens": 100, "completion_tokens": 50}` and `total_cost_usd = 0.0042`, when the turn completes, then `total_cost_usd == 0.0042` on the snapshot — the helper does not zero-out non-zero values just because the backend is local.

6. **Ambiguous dual-shape usage prefers Anthropic keys.** Given a `ResultMessage` with `usage = {"input_tokens": 100, "output_tokens": 50, "prompt_tokens": 9999, "completion_tokens": 9999}`, when the helper extracts, then `(per_turn_input, per_turn_output) == (100, 50)` and an INFO record is logged that mentions both key families.

7. **Anthropic-backed teammates remain bit-for-bit unaffected.** Given the existing `tests/test_e2e_token_cost.py::test_e2e_token_cost_pipeline` scenario (fakes emitting Anthropic-shape usage) run end-to-end after these changes, when the test runs, then it passes without modification (no edits to its fakes, fixtures, or assertions).

8. **Live-gated probe against the real local backend produces non-zero tokens.** Given the `CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1` env var is set AND a llama.cpp server is reachable at `${CLAUDE_CREW_LOCAL_LLAMA_URL:-http://127.0.0.1:8080}/v1/chat/completions` AND ccr at `${CLAUDE_CREW_LOCAL_CCR_URL:-http://127.0.0.1:3456}`, when the test spawns a real `SdkTeammate` with `local_backend=True` and sends a single short prompt, then `get_teammate_status(tid)["total_input_tokens"] > 0` AND `["total_output_tokens"] > 0` within 120s. **Skipped by default** (gate-env unset) — does not run in CI.

9. **Plugin-provided MCP servers are suppressed at spawn time.** Given a teammate spawned with a pack declaring `skills: [...]` (which triggers the SDK's `setting_sources=["user","project"]` auto-default) and a context-mode-like plugin installed at the user level with an `mcpServers` declaration in `plugin.json`, when the teammate is spawned, then `mcp__plugin_*` tools do NOT appear in the teammate's reported tool catalog AND any attempted call to such a tool fails with the CLI's MCP-not-configured error. Test via the existing fake-CLI / SDK-options-inspection harness: capture the `ClaudeAgentOptions` constructed for the teammate and assert `options.extra_args.get("strict-mcp-config", "MISSING") is None` (key present, value None — the SDK pass-through for a bare flag). A live end-to-end probe of plugin-MCP tools NOT firing on a real teammate is acceptable but optional (may be skipped if not feasible from the test substrate).

## Test Command

All test files import from packages already in the project manifest:
`pytest`, `pytest-asyncio`, `httpx` (for the local-backend probe; already a
dependency), and the in-repo `tests.fakes.sdk` helpers. No new system-level
prerequisites for the default suite. The live-gated AT#8 is skipped unless
both `CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1` is set AND a llama.cpp + ccr
pair is reachable; CI does not set these.

```bash
uv run pytest tests/test_sdk_teammate_local_attribution.py tests/test_e2e_local_token_attribution.py tests/test_sdk_teammate_strict_mcp.py tests/test_e2e_token_cost.py -v
```

## Out of Scope

- **Synthetic non-zero cost** (GPU-hour amortization, per-token energy cost, configurable price) — deferred; defaults to `$0.00` and flows through whatever `ResultMessage.total_cost_usd` reports.
- **`/slots`-based token diffing fallback** — only useful if the OpenAI-shape `usage` parse fails AND a local server is reachable; complex, requires per-turn bookkeeping, and is unnecessary now that ccr passes usage through. May be revisited if a backend is found that strips usage.
- **Dashboard ctx-window rendering changes** — already shipped (`local_model_metrics` + ctx_window strategy); this slice does not touch `ui_server.py`'s `_build_local_instance` token-rendering path beyond what naturally falls out of non-zero `status_snapshot()` values.
- **Per-teammate model registry / cost-price config** — operator-preference feature; not in this slice.
- **Refactoring `_extract_token_cost_from_rm` into a standalone module** — keep it co-located with `_collect_response_text` where its caller lives.
- **Changing `LiveTeammateInfo.is_local` derivation** — already correct.
- **Per-pack opt-out of `--strict-mcp-config`** — not supported; claude-crew unconditionally honors the deny-by-default allowlist. If a future use case needs plugin-MCP exposure, it's a separate spec.

## Assumptions

- **ccr passes the upstream llama.cpp `usage` object verbatim through to the SDK** — *Default:* assume yes (this is the documented OpenAI-completions contract; ccr is a thin translator). — *Rationale:* the OpenAI completions API specifies `usage` on the response; ccr's job is Anthropic-API translation, not response shape rewriting. If the live probe (AT#8) reveals stripped usage, the fallback is `/slots` diffing — explicitly out of scope this slice and would be raised in the Open Question.
- **The Anthropic SDK's `ResultMessage.usage` is shape-preserving** — *Default:* assume the SDK does not rewrite `prompt_tokens` → `input_tokens`. — *Rationale:* the existing zero-attribution bug is consistent with shape-preservation (the SDK would have hidden the bug by translating keys). The fix is at the consumer, not the SDK.
- **No production caller of `_collect_response_text` exists outside `_handle_one_turn`** — *Default:* assume single caller; thread the new kwarg there only. — *Rationale:* grep of `_collect_response_text` returns one prod call + test fakes; adding a kwarg-with-default does not break test fakes.
- **`is_local` semantics match the broker's existing `LiveTeammateInfo.is_local` rule** — *Default:* `bool(self._env) and "ANTHROPIC_BASE_URL" in self._env`. — *Rationale:* identical rule; one source of truth used in two places; if the broker rule ever changes, both update together (extracted helper if it becomes painful).
- **Cost staying at `$0.00` for local teammates is the right honest default** — *Default:* yes. — *Rationale:* operator preference can be added later; misattributing a synthetic cost now creates dashboard noise without clear utility.
- **The new test fixture `text_response_with_openai_usage` lives in `tests/fakes/sdk.py` next to the existing helper** — *Default:* yes. — *Rationale:* discoverability; consistent with existing fakes pattern.
- **AT#8 (live probe) is gated by `CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1`** — *Default:* gate-env name pattern matches existing `CLAUDE_CREW_LIVE_TESTS` convention. — *Rationale:* CI must remain green without a local model server.
- **`--strict-mcp-config` is unconditional (set on every spawn)** — *Default:* yes. — *Rationale:* claude-crew always honors the allowlist contract; there is no current use case for plugin-MCP exposure. Operator override, if ever needed, is a future spec.

## Open Questions

(none)

## Validation

End-to-end validation exercises the user-visible promise: a teammate spawned with
`local_backend=True`, driven for two turns, surfaces non-zero token counts
through `get_teammate_status`; AND a spawned teammate's constructed
`ClaudeAgentOptions` carries `--strict-mcp-config` so plugin-MCP servers are
suppressed. Both use the **fake-SDK** / options-inspection path
(deterministic, no GPU dependency) — the live-server probe lives in AT#8
behind a gate and is **not** part of automated validation.

```bash
uv run pytest tests/test_e2e_local_token_attribution.py tests/test_sdk_teammate_strict_mcp.py -v && uv run pytest -v
```

## Task Breakout

```yaml
tasks:
  - name: extract-helper-dual-shape
    description: |
      Extend _extract_token_cost_from_rm in claude_crew/sdk_teammate.py to detect
      and parse OpenAI-shaped usage dicts (prompt_tokens / completion_tokens /
      prompt_tokens_details.cached_tokens) in addition to the existing
      Anthropic-shaped dicts. Add an is_local kwarg (default False) used only
      for diagnostic logging; the actual switch is key-shape presence. Apply
      the same dual-shape detection to the AssistantMessage.usage peak-invocation
      read inside _collect_response_text. Thread is_local through
      _collect_response_text's signature (default False). Author the new
      tests/fakes/sdk.py helper text_response_with_openai_usage alongside (kept
      in the same task so the unit tests below can use it).
    dependsOn: []
    acceptanceTests: [2, 3, 5, 6]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/fakes/sdk.py"
      - "tests/test_sdk_teammate_local_attribution.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_sdk_teammate_local_attribution.py -v

  - name: thread-is-local-from-handle-turn
    description: |
      Wire is_local from _handle_one_turn into _collect_response_text using the
      teammate's own env (mirrors broker rule: bool(self._env) and
      "ANTHROPIC_BASE_URL" in self._env). Add a focused test that monkeypatches
      _collect_response_text with a spy and asserts the kwarg is True when env
      carries ANTHROPIC_BASE_URL, False otherwise. No change to the broker.
    dependsOn: [extract-helper-dual-shape]
    acceptanceTests: [4]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_sdk_teammate_local_attribution.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_sdk_teammate_local_attribution.py -k is_local -v

  - name: e2e-local-attribution
    description: |
      End-to-end test that spawns an SdkTeammate via the broker with a
      local_backend-style env, patches the SDK with a fake emitting OpenAI-shape
      usage, drives one turn, and asserts the full status_snapshot pipeline:
      total_input_tokens, total_output_tokens, last_turn_input_tokens,
      last_turn_output_tokens are non-zero and match the fake's emitted values;
      total_cost_usd flows through from total_cost_usd. Also include the live-
      gated probe (AT#8) guarded by CLAUDE_CREW_LIVE_LOCAL_BACKEND_TESTS=1 and
      pytest.importorskip / pytest.skip patterns; the test must be a clean SKIP
      (not an ERROR) when the gate is unset or the servers are unreachable.
    dependsOn: [thread-is-local-from-handle-turn]
    acceptanceTests: [1, 8]
    taskTouches:
      - "tests/test_e2e_local_token_attribution.py"
      - "tests/fakes/sdk.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_e2e_local_token_attribution.py -v

  - name: non-regression-anthropic-path
    description: |
      Run the existing tests/test_e2e_token_cost.py suite after all three tasks
      above land to prove the Anthropic-backed pipeline is bit-for-bit
      unchanged. No production-code edits, no fixture edits — the test command
      itself is the deliverable; the task fails if any pre-existing assertion
      regresses. This task is the explicit gate on AT#7.
    dependsOn: [extract-helper-dual-shape, thread-is-local-from-handle-turn, e2e-local-attribution]
    acceptanceTests: [7]
    taskTouches:
      - "tests/test_e2e_token_cost.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_e2e_token_cost.py -v

  - name: strict-mcp-config-plugin-isolation
    description: |
      In claude_crew/sdk_teammate.py, where opts_kwargs["mcp_servers"] is
      written (the deny-by-default honor-pack-tools-allowlist block), also
      add the CLI flag --strict-mcp-config via the SDK's extra_args
      pass-through:
        opts_kwargs.setdefault("extra_args", {})["strict-mcp-config"] = None
      Merge with any existing extra_args rather than overwriting. This
      closes the plugin-provided MCP leak documented in the Design Decisions
      "Allowlist Completeness" section: plugin manifests in
      ~/.claude/plugins/cache/ auto-register MCP servers whenever
      setting_sources includes "user"; --strict-mcp-config tells the CLI to
      ignore all MCP sources except the explicit --mcp-config payload that
      claude-crew already writes (deny-by-default empty). Dependency-
      independent of the four token-attribution tasks (same surface, but no
      ordering requirement) — may run in parallel.
    dependsOn: []
    acceptanceTests: [9]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_sdk_teammate_strict_mcp.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_sdk_teammate_strict_mcp.py -v
```

## Design Notes

- The `_extract_token_cost_from_rm` helper is currently nested inside
  `_collect_response_text`. Keep it nested — pulling it out is a refactor with
  no behavior payoff and would force test edits.
- The fake-SDK approach for ATs 1-7 is deliberate: ccr/llama.cpp are not
  reproducible in CI, and the bug is fundamentally a key-shape parse bug — a
  fake emitting the OpenAI shape exercises the exact production code path.
  AT#8 exists only to catch the case where reality diverges from the assumption
  that ccr passes usage through.
- Detection precedence (Anthropic > OpenAI when both present) is defensive,
  not expected — it's there so a future SDK update that adds OpenAI-keys
  alongside Anthropic-keys for backward-compat doesn't silently double-count.
- The `is_local` kwarg is deliberately a hint, not a gate. Key-shape detection
  decides which branch runs. This means if a hypothetical Anthropic-backed
  teammate ever sees OpenAI-shape usage (impossible today, defensive tomorrow),
  attribution still works.
- The `strict-mcp-config-plugin-isolation` task and the four token-attribution
  tasks both edit `claude_crew/sdk_teammate.py`; the edits land in
  non-overlapping blocks (the helper / drain block vs. the options-construction
  block), so parallel execution is safe — but the implementor of whichever
  task lands second must rebase cleanly. The task is intentionally listed last
  in the YAML so the dispatch order is unambiguous.
