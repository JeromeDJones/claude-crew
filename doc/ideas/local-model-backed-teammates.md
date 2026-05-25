# Idea: Local-model-backed teammates (mixed local + Anthropic crews)

**Status:** Idea, ready to plan. Foundation validated 2026-05-24 (local Gemma-4
drives Claude Code agentic sessions). Self-contained brief: the idea, the proven
foundation, the integration seam, the hard-won learnings, and the proposed work.
Referenced from `doc/BACKLOG.md`.

---

## The idea

Let a claude-crew run **some** teammates against a **local GPU model** (via
claude-code-router / ccr) while the lead and judgment-heavy roles stay on the
Anthropic API. Run cheap, parallelizable, or privacy-sensitive work (mechanical
refactors, test runs, bulk edits, log triage) on free local compute; keep
high-stakes reasoning on Opus/Sonnet. Also enables on-prem / air-gapped crews.

**Today it's all-or-nothing per crew process** (see "The integration seam"): you
can route *every* teammate to the local model, but not a *mixed* crew. Closing
that gap is the proposed work.

---

## Proven foundation (validated 2026-05-24)

A local model can drive Claude Code agentic sessions — tool use + multi-turn — at
**~7s/turn after a ~57s cold start.** First local model to clear the bar
(Qwen3.5-35B never did). Full hardware/model setup:
`~/.claude/projects/-home-jerome/memory/local-llm.md`.

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
- **Router:** `ccr` v2.0.0, config `~/.claude-code-router/config.json`, :3456.

---

## The integration seam (how a teammate routes to the local model)

claude-crew teammates default to `CLAUDE_CREW_TEAMMATE_MODE=sdk` and run on
`claude-agent-sdk`'s `ClaudeSDKClient` (`claude_crew/sdk_teammate.py`). The SDK
options set **no explicit `env`**, so the underlying CLI subprocess **inherits the
parent process `os.environ`**. Therefore:

> Launch the claude-crew lead / MCP-server process with the env below, and every
> SDK teammate it spawns routes through ccr → local model. No per-teammate code
> today.

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:3456"   # ccr
export ANTHROPIC_API_KEY="sk-local-no-key-required" # any non-empty value;
                                                    # satisfies claude_crew/auth.py
export CLAUDE_CODE_ATTRIBUTION_HEADER="0"           # MANDATORY — see gotcha #1
# then start the crew / lead as usual
```

- **Model string is cosmetic under ccr.** ccr routes by its own `Router` config
  (default → `llama-cpp,<model>`); the per-teammate `model=` resolved in
  `factories.py` doesn't pick the backend — ccr does.

---

## Learnings / gotchas (the hard-won part)

These cost a full debugging session. All three had to land together for tool use
+ acceptable latency; the prereqs below apply to any local-routed teammate.

### 1. `CLAUDE_CODE_ATTRIBUTION_HEADER=0` — the cache killer
Claude Code (≥ v2.1.29) injects `x-anthropic-billing-header: cc_version=...;
cch=<rotating-hash>;` as the **first text of the system prompt**. `cch` changes
nearly every request and sits at char ~0, truncating the KV-cache
longest-common-prefix to ~20 of ~25k tokens (llama.cpp reports `sim=0.001`) →
**full prompt reprocess every turn (~3-4 min)**. Set
`CLAUDE_CODE_ATTRIBUTION_HEADER=0` to remove it. `ENABLE_TELEMETRY=0` and
`DISABLE_NONESSENTIAL_TRAFFIC=1` do **not** remove it. After fixing: prefix
byte-stable, turns drop to ~7s.

### 2. llama.cpp needs `--swa-full` + `--cache-reuse` for Gemma
Gemma is a Sliding-Window-Attention model. Default llama.cpp keeps only a
window-sized KV cache and can't retain the prefix across requests → reprocess.
`--swa-full` keeps the full SWA cache (costs VRAM — we dropped ctx 128K→32K and
`-ngl` to 22 to fit 16GB). `--cache-reuse 256` lets it KV-shift past a mid-prompt
divergence (salvaged ~75% even across the header-removal change).

### 3. Route ccr at `/v1/chat/completions`, not `/v1/messages`
llama.cpp's Anthropic `/v1/messages` endpoint **mangles tool calls** (empty tool
name, double-encoded args → ccr 500s). Its OpenAI `/v1/chat/completions` endpoint
is clean. Set ccr `api_base_url` to `.../v1/chat/completions` and let ccr do the
Anthropic↔OpenAI translation. Also pass `--jinja` to llama.cpp (required for the
tool-call template to parse at all).

### Concurrency: one local GPU ≈ one concurrent heavy session
`run-gemma.sh` uses `--parallel 1` → **one slot**. Multiple teammates hitting one
local server concurrently serialize and evict each other's prefix cache,
collapsing back to slow reprocessing. For a crew of local-backed teammates either:
raise `--parallel N` (splits the context window — each slot gets ctx/N, so bump
`--ctx-size` and watch VRAM), accept serialized execution, or run separate
llama.cpp servers on different ports + ccr providers. **Plan crew size around the
slot count.**

### Verify caching against REAL requests, not a synthetic probe
A *synthetic* two-turn test — feeding llama.cpp a byte-identical prefix twice —
will show a huge speedup (we measured 29×) **even when the real Claude Code path
is still reprocessing every turn.** The synthetic prefix is stable by
construction; the real ccr path had the rotating `cch` busting it. **Validate
against captured real requests:**
- ccr requests (`~/.claude-code-router/logs/ccr-*.log`): the `"system":[...]`
  array prefix must be byte-identical across turns (no `x-anthropic-billing-header`).
- llama.cpp log (`/tmp/gemma-server.log`): `selected slot by LCP similarity,
  sim_best = X` should be ~0.99 (not 0.001) on turn 2+, and `prompt eval time =
  ... / N tokens` should show small N (only new tokens) on **actual agent turns**.

### Known ceiling
Gemma-4 handles tool use + multi-turn agentic work; capability under heavy
multi-tool load is the real limit, not the plumbing. It's a reasoning model (we
run `--reasoning-budget 0`). Cold-start prefill (~57s for 25k tokens) is the one
unfixable-on-this-hardware rough edge; lever is more GPU layers vs the VRAM
`--swa-full` consumes.

---

## Proposed work — per-teammate model/base_url override

Make routing **per-teammate** instead of per-crew-process.

- **Where**: `claude_crew/sdk_teammate.py` builds `ClaudeAgentOptions(**opts_kwargs)`
  (~line 1221) with no `env`/`base_url`. Thread an optional `env` (or explicit
  `base_url`/`api_key`) override from spawn-time through `broker.py`
  (`spawn_teammate` factory signature already carries `model`/`effort`/`cwd`) and
  `factories.py` into the SDK options.
- **Open question (verify first)**: confirm `claude-agent-sdk`'s
  `ClaudeAgentOptions` exposes an `env` passthrough to the CLI subprocess against
  the installed SDK version. If not, may need a subprocess-mode path.
- **Design in**: the concurrency cap (limit local-routed teammates to slot count)
  and the attribution-header prereq (whatever sets per-teammate env must set
  `CLAUDE_CODE_ATTRIBUTION_HEADER=0` for local-routed teammates).
- **Tests**: at the broker layer (override propagates through `spawn_teammate`)
  and the sdk_teammate layer (override reaches `ClaudeAgentOptions`; inherited-env
  default not regressed).
- **Size**: **M**. Cost driver: confirming the SDK honors per-spawn env without
  regressing the inherited-env default.

---

## References
- `~/.claude/projects/-home-jerome/memory/local-llm.md` — full hardware + model setup
- `~/dev/llama.cpp/run-gemma.sh` — the validated launch script
- `doc/research/sdk-*.md` — SDK teammate behavior notes
