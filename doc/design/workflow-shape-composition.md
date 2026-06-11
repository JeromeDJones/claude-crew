# Workflow Shape Composition — design thesis (brainstorm, 2026-06-11)

Status: **brainstorm / converging.** Not a spec. Captured mid-conversation so the thesis survives compaction.
Authors: Jerome (product direction) + Kael.

## The capability (what Jerome actually wants)

Not a communication feature. **The lead composes a right-sized workflow shape for the problem in front of it.**

- RepoReactor stops being *the* workflow and becomes **one shape** in a space of shapes — the heavyweight one.
- A typo gets a 2-node graph (implementor + reviewer, maybe just the lead).
- A gnarly feature gets the full plan → task-DAG → review → verify → document machine.
- The lead looks at the problem, **picks the agents, wires the topology, sets the gates, runs it.**

The "communication graph" Jerome opened with was the *substrate* this needs, not the point. The point is
matching **workflow weight to problem weight** — RepoReactor today is one-size-fits-all, and the overkill on
small work is the felt pain.

## The organizing principle: blessed shapes + bounded adaptation

The fork that matters:
1. **Right-sizing from a small library of blessed, parameterized shapes** — lead as **router + tuner**. High
   value, low risk, every shape is one the human already knows how to read and trust. ← **BUILD THIS FIRST.**
2. **Open-ended topology synthesis** — lead invents arbitrary novel graphs. Seductive, higher risk; the field's
   experience suggests flat/negative returns (verify in research). ← **earn it later.**

**Jerome's landing (2026-06-11):** select from blessed shapes, **and adapt if needed.** Not rigid
preset-selection (brittle), not open synthesis (too much rope). A running graph is always
**"known blessed shape + a small, readable diff."** That diff is the whole game — a human can trust
"heavy-feature shape + the project's co-architect on the planner node" at a glance; they could never trust an
arbitrary graph the lead dreamed up. **Legibility is the feature; bounded adaptation preserves it.**

Autonomy arc (agreed): prove shapes-against-sizing under the human-approved shape-gate FIRST; **then** widen the
lead's latitude toward autonomous adaptation. Earn the rope.

## Adaptation = a small algebra of operations on a shape

Five-ish verbs. Every real workflow = a blessed shape plus a handful of these:
- `add_node(role)` — insert a co-architect / extra reviewer
- `swap(slot, role)` — replace a built-in role with the project's
- `augment(node, validator)` — attach a second validator alongside a node *(Jerome's co-architect example)*
- `set_gate(edge, mode)` — strengthen / loosen a gate
- `drop(node|edge)` — trim for right-sizing

Expressive enough for "use this project's Agent as a co-architect"; constrained enough to stay readable.

## Edges carry a routing policy (the comms substrate, reframed)

The coordinator's role splits into **relay** (mechanical — safe to remove) and **gate** (judgment: is this
finding legit, is the loop converged, escalate/accept — load-bearing, must stay). Per-edge policy lets us delete
the relay while keeping the gate where it earns its seat:
- **`direct`** — A→B flows without a lead turn; broker still logs + sequences + surfaces every message. Needs a
  circuit breaker (max exchanges / token budget / deadlock detection) — this is the autonomous-chatter risk.
- **`tee`** — A→B direct, lead gets a copy and can interrupt. Human on every wire, blocking none.
- **`gated`** — A→B lands in lead's inbox first; lead approves/forwards. Today's behavior, made explicit;
  reserved for judgment edges (e.g. implementor↔slice-reviewer verdict loop).

## Why this passes the "coordinator-in-the-loop is the moat" acid test

(See memory `coordinator-in-the-loop-is-the-moat`; #20 peer-messaging was REJECTED 2026-05-17.)
The acid test: *does this give the operator more visibility/leverage, or let the crew operate further from the
operator's eye?* This passes **if and only if**:
- All edges route through the broker → fully logged + surfaced to Mission Control. Observable by construction.
- The coordinator stays a first-class participant (gate or tee) on the edges that matter.
- **The chosen shape is itself the primary gate** — before any agent spawns, the lead surfaces *"here's the
  graph I propose to run"* (blessed shape + adaptation diff) to the dashboard; human approves or tweaks. The moat
  moves UP a level: gating the **composition of the workflow**, not just messages. Nobody else has this because
  nobody else has the live supervised dashboard to put it on.

**Why now (the May rejection no longer holds):** #20 was rejected partly for "no logged friction." We've since
filed a MEDIUM (2026-06-04): the lead burns ~80% of its context window just orchestrating one feature, much of it
mechanical relay. A `direct`/`tee` clarification edge takes that traffic out of the lead's window while keeping it
on the dashboard. Concrete, logged friction this relieves.

## Current structural reality (verified in code 2026-06-11)

Hub-and-spoke isn't a convention — it's structural. A teammate can only reply to its sender
(`sdk_teammate.py:1706` hardcodes `recipient=env.sender`); the 12 `send_to`/`broadcast`/etc. MCP tools are the
**lead's surface only** — teammates don't hold them. The spokes have no way to find each other. That's what this
changes: teammates gain a `send_to` **scoped to their declared edges** — the graph IS the authorization layer.

## Connection to queued work: extensible-roles IS the `swap`/`augment` primitive

`doc/plans/extensible-roles-idea.md` (QUEUED) — "a project supplies its own feature-reviewer that replaces RR's
built-in one." We scoped it as a one-off reviewer swap. It's actually **the role-substitution verb of this
algebra.** Argues for building it *as* that primitive, not as a one-off. "proven to be a better validator" → the
adaptation reason is **accumulated project evidence**; RR already keeps project-scoped memory, so adaptation can
become memory-informed (start human-directed → grow to memory-suggested).

## Competitive / SDK posture (research-confirmed 2026-06-11)

Two senses of "works with Claude": (1) uses Claude as a **model** — every framework does this; (2) built on the
**Claude Agent SDK** runtime (`claude-agent-sdk`: tools, subagents, hooks, MCP, the CLI subprocess, the thing
claude-crew is bonded to). **CONFIRMED across 4 research passes: no external framework runs on the Agent SDK
runtime — all use Claude only as a model.** LangGraph (treats the SDK as a competitor; its "Deep Agents" is an
explicit reimplementation), Microsoft Agent Framework (Claude via `AnthropicClient`), CrewAI (via LiteLLM),
OpenAI Agents SDK, Google ADK — all alternative runtimes. **No framework gives us the graph engine AND runs on
the Agent SDK** — adopting one means abandoning the SDK substrate + the dashboard/hooks observability bonded to
it. "Build on claude-crew" isn't NIH; the alternatives are mutually exclusive with the differentiator. Right
move: **borrow the ideas, not the runtime** (LangGraph conditional-edges/`Command` handoffs; Magentic-One
task-ledger/progress-ledger as design vocabulary).

### ⚠️ The fact that changes the framing: Anthropic shipped "dynamic workflows" (2026-05-28)

The first-party **`Workflow` tool** (Claude Code v2.1.154+ / TS SDK v0.3.149+) lets Claude **write a JS
orchestration script that right-sizes and fans out up-to-~1,000 subagents at runtime**, executing outside the
conversation context, with a `/workflows` live progress view and an approve-the-plan-before-run gate. **This is a
large chunk of "the lead composes a right-sized workflow" — shipped, first-party, and already in Kael's toolset.**
It validates the *direction*. What it does NOT do, and where our thesis still stands apart:
- Topology is **model-emitted imperative JS**, not **declarative blessed shapes with a legible adaptation diff**.
- Supervision is **watch + approve-the-plan**, NOT **fine-grained mid-run steering of individual agents**
  (`send_to`) — Anthropic's own docs flag this as the gap.
- Subagents are **ephemeral fan-out workers**, not **persistent, messageable, recursively-delegating teammates**.
- No **per-edge gate policy** and no **shape-as-the-primary-gate** human checkpoint.

**Strategic fork — ✅ RESOLVED 2026-06-11 (Jerome): (a) build NATIVE.** We build the shape engine natively in
claude-crew — our own DAG runner over the existing SdkTeammate/broker substrate — and do **not** build on the
`Workflow` tool. Owning the substrate is the point: the four things `Workflow` lacks (declarative legible shapes,
mid-run `send_to` steering, persistent messageable teammates, per-edge gate + shape-as-gate) *are* the product, so
we will not anchor them to a primitive that can't express or be steered through them. The DAG-runner build is
accepted as the cost of full control. (Option (b) — compile blessed shapes onto the `Workflow` engine — is
rejected.)

### Closest full-vision competitor: Microsoft Agent Framework (GA 2026-04-02)

MAF's **Magentic** orchestrator (task-ledger/progress-ledger, dynamic runtime re-tasking) + **DevUI** (live
real-time graph/message/tool visualization) + first-class pause/resume/approval HITL is the nearest thing to the
*whole* vision now — dynamic right-sizing AND live supervision. Our edge: it uses Claude as a model only; DevUI is
debugging-oriented, not coordinator-in-the-loop with mid-run `send_to`; no shape-as-gate. Real, and ahead on
polish — watch it.

### Academic evidence — VINDICATES the thesis, with a correction to an earlier overstatement

Earlier in this conversation Kael said "LLMs designing their own elaborate topologies tend to underperform simple
templates." **The research says that's too strong as stated — and sharpens it in our favor.** The real dividing
line is **right-sizing per task, NOT elaboration**:
- **What fails:** elaborate, *static, one-size-fits-all* auto-designed topologies applied uniformly (MaAS, ICML
  2025); MAS "gains" that vanish under **matched compute** vs a strong single-agent baseline (2026 single-agent
  papers; Berkeley MAST 1600-trace failure study — most failures are coordination/design, not model). Cognition's
  "Don't Build Multi-Agents" → "what's actually working" converges on **a few simple templates**.
- **What wins:** auto-composition that is **difficulty/query-conditioned over a constrained modular space** beats
  both human templates and naive auto-design on accuracy *and* cost (MaAS: 6–45% of the inference cost;
  AgentSquare: +17.2%).
- **Our design sits exactly on the winning side:** blessed shapes (constrained modular space) + right-size to
  problem (difficulty-conditioned) + bounded adaptation (not freeform elaboration). **The empirical case for
  "blessed shapes + bounded adaptation, NOT open synthesis" is now evidence-backed, not just instinct.** It also
  argues that the *biggest* measurable win is the right-sizing itself (don't run the tank for a typo) — that
  should be the thing M0/M1 proves.

## Implication: rewrite RepoReactor from the ground up

Jerome (2026-06-11): once this substrate exists, repo-react should NOT be ported — **re-expressed as one blessed
shape (the heavy one) authored natively in the new model.** Current repo-react carries scars from being the only
workflow. Ground-up rewrite is the honest move.

## First shapes worth blessing (proposed)

- `micro-fix` — lead + reviewer (or just lead)
- `standard-feature` — plan → implement → review (no DAG)
- `heavy-feature` — today's full RepoReactor, re-authored natively

These three probably cover ~90% of real work; the gap between `micro-fix` and `heavy-feature` is exactly the
overkill complaint that started this.

## Open decisions

1. **Who decides "adapt if needed"?** human-directed only (v1 floor) → lead-proposed/human-approved (target) →
   lead-autonomous (deferred, earn it). Jerome wants to grow toward more lead autonomy once shapes-vs-sizing is
   proven.
2. Default edge mode — ✅ **RESOLVED 2026-06-11 (Jerome): `gated`-first.** New edges default to `gated` (lead
   inbox first); `tee`/`direct` are opt-in per edge, earned deliberately. The conservative default keeps the
   coordinator on every edge by default — loosening a gate is an explicit act, never the resting state.
3. Exact shape-template format + the adaptation-op API surface.
4. How the shape-gate renders + approves on Mission Control (lean on the shipped diagram-viz/mermaid layer).
