# Kick-off prompt: local-model token & cost attribution

Paste the block below into a fresh Kael session. Self-contained — assumes no
memory of the conversation that produced it.

---

We're ready to wire **local-model token & cost attribution** into claude-crew
so Qwen-backed (or any local) teammates show real tokens / cost on
`get_teammate_status`, the dashboard, and the broker's per-turn telemetry —
the same shape Anthropic-backed teammates already report.

## What's done (landed on master)

- **`honor-pack-tools-allowlist`** (commit `c683922` once merged) — pack
  `tools:` is now the wire-level catalog AND pre-approval set; MCP servers
  are deny-by-default. Qwen-backed teammates now get a small wire prompt
  (was 107 KB / 35 tools, now ~3 KB / 3 tools for the default `explorer`)
  and turn-1 latency dropped from ~13 min to seconds on Qwen-3.6 35B.
- **`local_model_metrics`** slice (commit `89270b9`) — parses llama.cpp
  `/slots` for ctx-window metrics.
- **Dashboard ctx-window strategy** (commit `dfc2a76`) — one sink, local +
  anthropic sources. The `/slots` probe is non-blocking + cached (`eed89d9`
  + `0637206`). Don't re-pave this; build alongside.
- **Per-teammate local ctx-window attribution at `--parallel N`** —
  backlog noted (`24540a5`); may overlap with this work.

## The gap

`get_teammate_status` on a local-backend teammate currently shows:
```
total_input_tokens: 0
total_output_tokens: 0
total_cost_usd: 0
last_turn_input_tokens: 0
last_turn_output_tokens: 0
last_turn_peak_invocation_input_tokens: 0
active_model: Qwen3.6-35B-A3B-UD-Q4_K_M.gguf   ← model known
```

`active_model` is correct; everything else is `0` because
`_collect_response_text` in `claude_crew/sdk_teammate.py` reads
`ResultMessage.usage` per Feature #14 D-1, which is populated only by the
Anthropic API path. The local path (Anthropic format → ccr → llama.cpp
`/v1/chat/completions`) doesn't populate it the same way.

## What we need

Per-turn input/output tokens (and an estimated/computed cost) attributed to
local-model teammates, surfaced through the same path Anthropic uses so the
dashboard and `get_teammate_status` work uniformly. "Cost" for local can be
zero or a synthetic price (per-token energy cost, GPU-hour amortization, or
explicit zero with a flag) — design decision.

## First questions to answer (no code yet)

1. **Where do the tokens come from?** Options:
   - llama.cpp's `/v1/chat/completions` response includes `usage` (OpenAI
     format) — does ccr pass that through to the SDK?
   - The SDK's `ResultMessage` is constructed from the upstream response —
     does it populate `usage` from OpenAI-format `usage` when the response
     is non-Anthropic?
   - Fall back: read `/slots` for cumulative tokens and diff per turn (less
     accurate, requires more careful bookkeeping).
2. **What's the right insertion point?** Probably `_collect_response_text`,
   but verify by tracing what `ResultMessage.usage` looks like in a real
   local-backend turn (use the live llama+ccr already running on `:8080` /
   `:3456`).
3. **Cost accounting.** Zero is honest. A synthetic non-zero might be more
   useful for relative-cost comparisons. Operator preference.
4. **Cache attribution.** llama.cpp reports cache hits via `/slots`; the
   prompt-cache portion of input is a meaningful number for local inference
   economics.

## Process

This is significant work. Use the **repo-reactor flow** per Kael guidance:
- Spawn `repo-reactor:rr-planner` to read the relevant code and produce
  a structured feature spec.
- `rr-plan-reviewer` adversarial review.
- `rr-implementor` builds against an approved spec.
- BDD-first per Jerome's preference — tests pin the observable behavior
  (`get_teammate_status` returns non-zero tokens for a local-backend
  teammate after one turn).

## Files to read first

- `claude_crew/sdk_teammate.py` — `_collect_response_text` and the
  `_handle_one_turn` / `_end_turn` flow.
- `claude_crew/broker.py` — per-teammate telemetry accumulation.
- `claude_crew/local_model_metrics.py` — `/slots` parser (existing).
- `claude_crew/ui_server.py` — ctx-window strategy (existing, don't
  duplicate).
- `doc/ideas/local-model-backed-teammates.md` — architectural context
  (ccr at `:3456`, llama.cpp at `:8080`, the model-name cosmetic note).
- `doc/BACKLOG.md` — search for "local" and "token" to find prior notes.

## Live test environment (still running)

- llama.cpp Qwen 3.6 server: `http://127.0.0.1:8080`
- ccr (Anthropic → OpenAI translation): `http://127.0.0.1:3456`
- Spin down with `kill $(cat /tmp/qwen36-test/llama2.pid); ccr stop` if
  the testing path moves elsewhere.

Spawn a teammate with `local_backend=True` to drive end-to-end. The Qwen
backend has prompt cache warmed from prior session — turn 1 will be
sub-30s on a small explorer.

## What to NOT touch

- The non-blocking `/slots` probe & dashboard ctx-window strategy. Already
  shipped; build alongside.
- The honor-pack-tools-allowlist contract. Done; build local-attribution
  ON TOP of it.

Begin by reading the files above, then spawn the planner with a tight
problem statement.
