# Local-Model-Backed Teammates

**Status:** Validated recipe (2026-05-24). Local Gemma-4 drives Claude Code agentic
sessions (tool use + multi-turn) at ~7s/turn after a ~57s cold start. This doc
captures the working stack and the non-obvious gotchas so we can spin up a
claude-crew teammate backed by a local model instead of the Anthropic API.

## Why

Run select teammates (bulk/mechanical work, privacy-sensitive tasks, cost-free
iteration) against a local GPU model while the lead and judgment-heavy roles stay
on Anthropic. The integration seam already exists — no claude-crew code change
required.

## The stack

```
Claude Code / SDK teammate ──Anthropic format──▶ claude-code-router (ccr, :3456)
        │                                              │ Anthropic↔OpenAI transform
        │                                              ▼
        └──────────────────────────────────  llama.cpp server (:8080, OpenAI API)
                                                       │
                                                       ▼
                                          Gemma-4-26B-A4B-it (ROCm, RX 9070 16GB)
```

- **Model:** `~/models/gemma-4-26B-A4B-it-GGUF/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf`
  (Unsloth dynamic Q4, ~16GB). Reasoning + tool-calling MoE (26B total / 4B active).
- **Server launch:** `~/dev/llama.cpp/run-gemma.sh --server` (host :8080).
- **Router:** `ccr` v2.0.0, config `~/.claude-code-router/config.json`,
  listening :3456. `ccr start` / `ccr stop` / `ccr status`.

## How a teammate routes to the local model

claude-crew teammates default to `CLAUDE_CREW_TEAMMATE_MODE=sdk` and run on
`claude-agent-sdk`'s `ClaudeSDKClient` (`claude_crew/sdk_teammate.py`). The SDK
options here set **no explicit `env`**, so the underlying CLI subprocess
**inherits the parent process `os.environ`**. Therefore:

> Launch the claude-crew lead / MCP-server process with the env below, and every
> SDK teammate it spawns routes through ccr → local model. No per-teammate code.

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:3456"   # ccr
export ANTHROPIC_API_KEY="sk-local-no-key-required" # any non-empty value;
                                                    # satisfies claude_crew/auth.py
# then start the crew / lead as usual (e.g. `ccr code`, or your crew entrypoint)
```

- **Model string is cosmetic under ccr.** ccr routes by its own `Router` config
  (default → `llama-cpp,<model>`), so the per-teammate `model=` resolved in
  `factories.py` doesn't pick the backend — ccr does. (You *can* wire ccr
  model-name routing later if you want per-teammate local/remote split.)
- **Mixed crews:** to keep the lead on Anthropic and only *some* teammates local,
  you'd need per-teammate base_url — not yet plumbed (SDK options take no env
  here). Today it's all-or-nothing per crew process. Possible follow-up:
  thread an `env`/`base_url` override through `ClaudeAgentOptions`.

## Three gotchas that MUST be fixed (else it's unusable)

These cost a long debugging session. All three had to land together for tool use
+ acceptable latency.

### 1. `CLAUDE_CODE_ATTRIBUTION_HEADER=0` — the cache killer
Claude Code (≥ v2.1.29) injects `x-anthropic-billing-header: cc_version=...;
cch=<rotating-hash>;` as the **first text of the system prompt**. `cch` changes
nearly every request and sits at char ~0, truncating the KV-cache
longest-common-prefix to ~20 of ~25k tokens (llama.cpp reports `sim=0.001`) →
**full prompt reprocess every turn (~3-4 min)**. Set `CLAUDE_CODE_ATTRIBUTION_HEADER=0`
(env or `~/.claude/settings.json` env block) to remove it. `ENABLE_TELEMETRY=0`
and `DISABLE_NONESSENTIAL_TRAFFIC=1` do **not** remove it. After fixing: prefix
byte-stable, turns drop to ~7s.

### 2. llama.cpp needs `--swa-full` + `--cache-reuse` for Gemma
Gemma is a Sliding-Window-Attention model. Default llama.cpp keeps only a
window-sized KV cache and can't retain the prefix across requests → reprocess.
`--swa-full` keeps the full SWA cache (costs VRAM — we dropped ctx 128K→32K and
`-ngl` to 22 to fit on a 16GB card). `--cache-reuse 256` lets it KV-shift past a
mid-prompt divergence (salvaged ~75% even across the header-removal change).

### 3. Route ccr at `/v1/chat/completions`, not `/v1/messages`
llama.cpp's Anthropic `/v1/messages` endpoint **mangles tool calls** (empty tool
name, double-encoded args → ccr 500s). Its OpenAI `/v1/chat/completions` endpoint
is clean. Set ccr `api_base_url` to `.../v1/chat/completions` and let ccr do the
Anthropic↔OpenAI translation. Also pass `--jinja` to llama.cpp (required for the
tool-call template to parse at all).

## Concurrency caveat for crews
`run-gemma.sh` uses `--parallel 1` → **one slot**. Multiple teammates hitting one
local server concurrently will serialize and evict each other's prefix cache,
collapsing back to slow reprocessing. For a crew of local-backed teammates either:
- raise `--parallel N` (splits the context window across N slots — each gets
  ctx/N, so bump `--ctx-size` accordingly and watch VRAM), or
- accept serialized execution (fine for a single local worker), or
- run separate llama.cpp servers on different ports + ccr providers.

Single local GPU = effectively one concurrent heavy session. Plan crew size
around that.

## Verify it's working
- llama.cpp log (`/tmp/gemma-server.log`): `selected slot by LCP similarity,
  sim_best = X` should be ~0.99 (not 0.001) on turn 2+, and `prompt eval time =
  ... / N tokens` should show small N (only new tokens).
- ccr requests (`~/.claude-code-router/logs/ccr-*.log`): the `"system":[...]`
  array prefix must be byte-identical across turns (no `x-anthropic-billing-header`).

> **Debugging lesson (cost us a near-false-victory):** a *synthetic* two-turn
> cache test — feeding llama.cpp a byte-identical prefix twice — will show a huge
> speedup (we measured 29×) **even when the real Claude Code path is still
> reprocessing every turn.** The synthetic prefix is stable by construction; the
> real ccr path had the rotating `cch` header busting it. **Always validate
> caching against *captured real requests* (diff the `"system"` arrays in the ccr
> log), not a hand-built prefix.** The definitive signal is `sim_best` and the
> per-turn `prompt eval ... / N tokens` on *actual* agent turns, not a probe.

## Known ceiling
Gemma-4 handles tool use and multi-turn agentic work (first local model that
does — Qwen3.5-35B never cleared the bar). It's a reasoning model (burns thinking
tokens; we run `--reasoning-budget 0`). Capability under heavy multi-tool load is
the real limit, not the plumbing. Cold-start prefill (~57s for 25k tokens) is the
one unfixable-on-this-hardware rough edge; lever is more GPU layers vs the VRAM
`--swa-full` consumes.

See also: `~/.claude/projects/-home-jerome/memory/local-llm.md` (full hardware +
model setup), `doc/research/sdk-*.md` (SDK teammate behavior).
