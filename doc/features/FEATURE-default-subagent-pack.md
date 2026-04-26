# Feature: Default Subagent Pack

**Status**: In Progress (Phase 5 — awaiting Jerome's manual verification)
**Created**: 2026-04-25
**Branch**: `feat/default-subagent-pack`

---

## Phase 1: Research & Requirements

### Problem Statement

claude-crew teammates today are a single-conversation Claude with no
delegated subordinates. The product vision (capability #2) calls for
**recursive subagent decomposition** — the differentiator that breaks
Claude Code's one-level recursion ceiling. Without a default pack, every
operator has to author their own `AgentDefinition`s before a teammate can
delegate; the runtime is technically capable but practically empty.

Feature #3a ships the floor: three role-shaped subagents — `explorer`,
`planner`, `general-purpose` — bundled with claude-crew, configured into
every `SdkTeammate` on spawn, ready to be invoked via the SDK's `Task`
tool. Operators bringing their own definitions (Feature #3b) build on
top of this seam.

This feature also resolves the two SDK-behavior verification items the
vision flagged as gating #3a: subagent context isolation and per-subagent
token budgets. Both are answered empirically in `doc/research/sdk-subagents.md`.

### Success Criteria

Tightened against the co-architect review — each criterion is a contract,
not a research output.

- [ ] **SC-1: Subagent runs with the configured model and tool surface.**
  When a teammate invokes `Task(subagent_type="explorer")`, the run is
  observably executed by Haiku with the explorer's tool allowlist
  (Read/Grep/Glob only). Verifiable from `TaskNotificationMessage.usage`
  and the tool-use messages in the parent's stream.

- [ ] **SC-2: Context-isolation contract holds.** The pack documents and
  the implementation guarantees:
  - Subagents inherit parent's `setting_sources` (and therefore CLAUDE.md). *Documented as intentional.*
  - Subagents do NOT inherit parent's conversation history.
  - Subagents do NOT inherit parent's `system_prompt`.
  - Subagents share parent's `cwd`. *Documented.*

  **Always-runs verification** (CI-safe, no live API): a unit test
  imports the pack, constructs `ClaudeAgentOptions` with a parent
  `system_prompt="PARENT_MARKER"`, and asserts that no member of
  `options.agents` has `PARENT_MARKER` substring in any field — proving
  we never copy parent's prompt into subagents. **Live verification**
  (gated behind `CLAUDE_CREW_LIVE_TESTS=1`): SC-10's smoke test re-runs
  the isolation probes from `doc/research/sdk-subagents.md` so SDK
  upgrades that silently break isolation are caught.

- [ ] **SC-3: Per-subagent budget contract holds.** Each pack member's
  `maxTurns` and `effort` apply to that subagent's loop only and do not
  consume parent's session-level budget. Verified by a regression test
  that asserts truncation behavior under a tight `maxTurns`.

- [ ] **SC-4: Default models match the pack contract.**
  - explorer = `haiku`
  - planner = `sonnet`
  - general-purpose = `sonnet`

  Verified by reading the registered `AgentDefinition` objects and
  asserting against the contract.

- [ ] **SC-5: System prompts and pack metadata are committed in-repo.**
  No network fetch, no derived content. The pack is hermetic — same
  inputs, same pack, on a fresh clone. Verified by a unit test that
  imports the pack and asserts each member's prompt/description is the
  literal string in the source tree.

- [ ] **SC-6: A teammate spawned with no overrides automatically has all
  three subagents available.** Spawning via `mcp__claude-crew__spawn_teammate`
  with default args produces an `SdkTeammate` whose underlying
  `ClaudeAgentOptions.agents` dict has exactly the three pack keys with
  the contract-defined values. Verified by an integration test against
  the MCP server.

- [ ] **SC-7: Determinism.** Two teammates spawned with the same role
  in the same process get byte-identical pack configurations. No
  environment-conditional behavior, no silent fallbacks, no clock-based
  variance. Verified by spawning twice and asserting equality of the
  registered `AgentDefinition`s.

- [ ] **SC-8: Subagent failure contract.** Two failure modes, both
  contracted:
  - **(a) Graceful SDK-reported failure** — turn budget exhausted, tool
    denial, model-side error. Parent receives a
    `TaskNotificationMessage` with `status` in `{"failed", "stopped"}`.
    The teammate's `_handle_one_turn` lets the parent-stream draining
    finish normally; the response text the parent already has is
    delivered to the lead in a normal envelope (with whatever the
    parent said about the failure). No special error code required —
    the parent already narrates. **Edge:** if the parent's text buffer
    is empty when the failure arrives (subagent failed instantly,
    parent never produced text), the teammate synthesizes a minimal
    error envelope (`code="invalid_response"`) using
    `TaskNotificationMessage.summary` if present, or a default message
    if absent. The lead never gets an empty success envelope.
  - **(b) Stream-level exception** — `client.receive_response()` raises
    instead of yielding (subprocess crash, OOM, network drop, malformed
    SDK message). Caught in `_handle_one_turn`, classified as
    `code="internal"` per Feature #2's error envelope path, delivered to
    the lead. The teammate's worker task **stays alive** and processes
    the next inbox message normally — same survival contract as Feature
    #2's SC-6.

  Each mode gets a dedicated test:
  - (a) integration test with a tight `maxTurns=1` subagent that gets
    asked something it can't finish; assert envelope arrives, assert
    next message processes normally.
  - (b) unit test against `SdkTeammate` with a fake client whose
    `receive_response()` raises mid-stream; assert error envelope with
    `code="internal"` and that `_task` is still running.

  **Operator-visible logging.** On either failure mode, the teammate
  emits a `WARNING`-level log entry naming the subagent, the failure
  mode, and the SDK's `task_id`. Operators tailing stderr see
  subagent failures even before Feature #4's transcript widening
  ships. Format details are Phase 2; the existence of the log is
  Phase 1 contract.

- [ ] **SC-9: Internal override seam exists for #3b.** The pack
  registration code path accepts an explicit `agents` dict in addition
  to the default. Operator-facing API for that override is *out of
  scope* for #3a — but the seam is wired and tested, so #3b can ride on
  it without rework. Verified by passing a custom dict in a unit test
  and observing the resulting `ClaudeAgentOptions.agents`.

- [ ] **SC-10: Live smoke validates the end-to-end loop.** A gated live
  test (mirroring `scripts/sdk_smoke_test.py`) spawns a real teammate,
  asks it to invoke each of the three subagents in sequence, and
  asserts each completes successfully. Per-pack-member assertions:
  - **Tool-name correctness:** each subagent's run includes at least
    one observed tool call from a non-Read tool in its allowlist
    (planner: `Write`; general-purpose: `WebFetch` or `Edit`),
    confirming the SDK accepts our string values. Read-only explorer
    asserts only on Read tool-use.
  - **Model-alias routing:** the SDK does not expose model id in
    `TaskUsage`, and token-count comparisons across roles are workload-
    dependent (a Grep-heavy explorer can outspend a one-shot planner).
    We rely on the SDK's documented contract that `model="haiku"|
    "sonnet"|"opus"` aliases route deterministically, plus eyeball
    confirmation on the first live run. No flaky comparison test.
  - **Isolation regression:** repeats the parent-CLAUDE.md /
    parent-conversation / parent-system_prompt probes from
    `sdk-subagents.md` against the real pack, catching SDK upgrades
    that silently break isolation.

  Cost: ~$0.40. Gated on `CLAUDE_CREW_LIVE_TESTS=1`.

- [ ] **SC-11: Pack documentation includes a testable security
  section.** A `claude_crew/subagents/README.md` (or equivalent) ships
  with a section heading matching the regex `Security[: ].*CLAUDE\.md`.
  The section names the pack members with network access
  (`general-purpose` via `WebFetch` and `WebSearch`), states explicitly
  that subagents read CLAUDE.md content via inherited `setting_sources`,
  and recommends operators audit CLAUDE.md before relying on the
  default pack with sensitive content. Verified by a regex test in the
  suite.

### Resolved Decisions

These were debated with the co-architect during Phase 1; they are not
open for relitigation in Phase 2 absent new evidence.

- **Default models:** explorer=haiku, planner=sonnet, general-purpose=sonnet.
  Rationale: planner=Sonnet (not Opus) because per-subagent `effort` is the
  cheaper tunable lever, subagents are leaves so planner errors face
  downstream review (PR/builder), and default-pack economics matter — every
  spawned teammate inherits this cost floor.

- **Default `effort` per role:** explorer=`low`, planner=`high`,
  general-purpose=`medium`. Rationale: planner is where reasoning depth
  pays back; explorer is fast pattern-search; general-purpose is the
  unknown-shape default.

- **Tool allowlists** (explicit, deterministic — no `tools=None`):
  - explorer: `["Read", "Grep", "Glob"]`. Read role only.
  - planner: `["Read", "Grep", "Glob", "Write"]`. Can produce a spec doc; cannot mutate existing files (Edit excluded by intent — planner writes new artifacts, doesn't refactor).
  - general-purpose: `["Read", "Grep", "Glob", "Edit", "Write", "WebFetch", "WebSearch"]`. Notably **no Bash** — prompts are not security boundaries, and no default subagent should shell out unattended. **No Task** on any pack member — subagents are leaves, period.

- **`maxTurns` per role:** explorer=10, planner=20, general-purpose=20.
  Rationale: starting points; tunable in #3b. Explorer should be quick;
  planner and general-purpose may need multi-step research or
  iteration.

- **`background`:** `False` for all three. Async subagents change the
  parent's reasoning model and are out of scope.

- **`initialPrompt`:**
  - explorer: *yes* — "Begin by stating what you're searching for and where you'll look. Then proceed."
  - planner: *yes* — "Begin by restating the task in your own words and naming the acceptance criteria you will satisfy. Then proceed." (Structural scope-creep guard.)
  - general-purpose: *none*.

- **Context isolation posture (Option A — product principle):** Subagents
  inherit the teammate's `setting_sources` and therefore CLAUDE.md. This
  is intentional: a planner spawned by your teammate should know your
  standing instructions the same way the teammate does. Operators who
  need a clean-room subagent spawn a teammate with `setting_sources=[]`;
  isolation is a teammate-level concern, not a subagent-level knob. (This
  is enforced by the SDK; we are aligning the product framing with it.)

- **Security note (must ship in pack docs):** Subagents see the operator's
  CLAUDE.md. Subagents with network tools (general-purpose has WebFetch /
  WebSearch) can therefore exfiltrate CLAUDE.md content. Operators who
  put secrets, internal hostnames, or NDA'd identifiers in CLAUDE.md
  must understand this. One paragraph in the pack README.

- **System prompt sourcing:** hand-written by us, in-repo, with explicit
  **"contract" sections** distinguishing must/must-not behaviors from
  voice. Prompts encode load-bearing invariants (planner restates
  acceptance criteria; explorer doesn't write; general-purpose doesn't
  shell out). Not derived from Claude Code's built-in agents.

- **Loader contract:** Phase 2 will decide between (a) `.md`-on-disk
  loaded at process start vs (b) Python literals in
  `claude_crew/subagents.py`. Both produce a `dict[str, AgentDefinition]`
  passed to `ClaudeAgentOptions.agents`. SDK does not constrain this; we
  pick what makes #3b's user-defined-agent loader cleanest. **Open in
  Phase 2.**

- **Pack composition:** all teammates get all three subagents in #3a.
  Per-teammate selection is #3b or later.

- **Override semantics for #3b (pinned now to keep Phase 2 shape-only):**
  Per-key override at the **whole-AgentDefinition** level, user wins on
  collision. If a user-defined agent shares a key with a default pack
  member, the user's full definition replaces ours; non-conflicting
  keys merge. No field-level merging — redefining one knob means
  redefining the whole entry. Rationale: replace-all is a footgun
  (silent loss of pack members); field-level merge makes "where did
  this value come from" unanswerable. Phase 2 designs the merge
  function; semantics are settled.

- **Subagent observability:** Out of scope for #3a. Feature #4's
  transcript widening is the home for it. Spike confirmed it is
  feasible without architectural surgery (`TaskStartedMessage` /
  `TaskNotificationMessage` carry the needed fields).

### Out of Scope (Explicit)

- **Loading user-defined `~/.claude/agents/*.md`** — Feature #3b.
- **Per-teammate subagent set selection** — Feature #3b.
- **Operator-facing API for overrides** (custom subagents at spawn time) — Feature #3b. Internal seam (SC-9) ships now.
- **Subagent-to-subagent invocation** — subagents are leaves. Locked.
- **Subagent activity in the JSONL transcript** — Feature #4 widens transcript scope; #3a leaves the gap with a documented entry in the risk register below.
- **Dynamic / runtime pack composition** — pack is frozen at spawn.
- **Prompt version migration for in-flight teammates** — pack content is frozen at the teammate's `start()` call. Operators get changes by killing and respawning.
- **Tool restrictions beyond the static allowlists declared above** — no `permissionMode` knobs exposed to operators in #3a.
- **`memory` field configuration** — per #2 research the SDK doesn't activate auto-memory; field is a no-op for now.
- **Concurrent subagent invocation within one teammate turn.** Per Feature #2's design, a teammate processes its inbox **strictly serially** — one envelope to completion (including any subagent calls that envelope triggers) before pulling the next. Subagent invocations within that one turn run sequentially under the SDK's own scheduling; we do not parallelize them. This is load-bearing for #3b's per-teammate selection design and for #5's real-task validation; stating it here so it isn't relitigated later.

### Risk Register

- **Subagent observability gap.** #3a ships subagents that the JSONL
  transcript cannot see (it logs lead↔teammate only). Operators
  debugging a misbehaving subagent will lack signal until #4 widens
  scope. *Mitigation:* spike confirmed #4 is small; prioritize after #3a.
- **CLAUDE.md exfiltration via general-purpose's WebFetch / WebSearch.**
  Documented in pack docs; not technically mitigated. The mitigation is
  operator awareness. Future feature could add prompt-time stripping or
  permissionMode='deny' for network tools when CLAUDE.md contains
  flagged content — neither is in scope.

### Follow-up Verifications (post-merge)

- **Top-level teammate auto-memory access** — `sdk-memory.md` concluded
  the SDK does not activate Claude Code's auto-memory subsystem
  (`~/.claude/projects/<encoded-cwd>/memory/MEMORY.md`), but that test
  was indirect (asked the model what memories it had; never tried to
  directly read/write the directory). After #3a merges, send a probe
  message to a live teammate (the co-architect is a good candidate)
  asking it to read and write its own auto-memory file directly. Two
  turns, ~$0.02. Update `sdk-memory.md` with the result and, if
  positive, add a vision verification line for "teammate auto-memory
  read/write contract."
- **`AgentDefinition.memory` selector behavior** — same probe, but
  scoped to a subagent configured with `memory="user"`. Determine
  whether the field is a no-op or selects a real memory scope for the
  subagent's loop.

### Constraints & Dependencies

- **Requires:** `SdkTeammate` (Feature #2, shipped) — this is where the
  pack is plumbed in. Specifically `ClaudeAgentOptions.agents` is set
  from a new module-level pack registry.
- **Requires:** `claude_agent_sdk` v0.1.68+ (`AgentDefinition` shape
  pinned to current fields).
- **Breaking changes:** No — adding subagents to the parent's options is
  additive. Existing tests that don't exercise the Task tool keep
  passing.
- **Performance implications:** A teammate's first turn now carries the
  pack registration in the SDK initialize request. Empirically the spike
  ran clean; size is small (three definitions). Worth confirming the
  init-time SDK round-trip isn't slowed; not expected to be a problem.
- **Cost implications:** Subagent invocations cost real money (per
  `TaskNotificationMessage.usage` in spike: planner-shaped runs were
  ~40k tokens). Operators who don't invoke subagents pay nothing extra;
  pack registration alone is free.

### Open Questions

- [ ] **Loader contract — `.md` files vs Python literals.** Both work
  for #3a. The trade-off is what #3b inherits cleanly. If user-defined
  agents will live in `~/.claude/agents/*.md` (the vision row spec for
  #3b), then a `.md` loader for our defaults exercises the same code
  path and proves it. If we go Python literals for our defaults, #3b
  builds an entirely separate loader. **Lean: ship `.md` files in
  `claude_crew/subagents/{explorer,planner,general-purpose}.md` and
  build the loader as part of #3a. Costs ~1 task; pays back immediately
  in #3b.** Resolve in Phase 2 design.

**Gate**: Questions answered, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Phase-2 Carryovers (from Phase 1 Sentinel review)

These are deferred-from-Phase-1 items the design must address:

- ~~**`effort` per-subagent live verification.**~~ *Resolved during gate
  review — flaky comparison cut. SDK's documented per-agent `effort`
  contract is taken on faith; eyeball-confirmed on first live run.*
- **Seam-shape merge function.** Semantics pinned in Phase 1 (per-key
  override at whole-AgentDefinition level, user wins). Phase 2 designs
  the actual merge function and where it lives in the call chain.
- **Loader contract (Open Question Q1).** `.md` files in
  `claude_crew/subagents/` vs. Python literals. Lean: `.md`, because
  #3b's user-defined-agent loader will exercise the same path. Decide
  in Phase 2.
- **Teammate-side logging on subagent failure.** Even with transcript
  widening deferred to #4, Phase 2 should specify a
  `logger.warning`-level entry in the teammate when SC-8 (a) or (b)
  fires, so operators tailing stderr have *some* signal before #4
  ships.
- **Cost-cap guardrails.** No per-teammate or per-turn cost cap is in
  scope for #3a, but Phase 2 should explicitly state that the runtime
  relies on per-subagent `maxTurns` plus operator visibility into
  `TaskNotificationMessage.usage` as the only guardrails. If that's
  insufficient, it's a separate feature.

### Architecture Overview

```
claude_crew/
├── subagents/                    ← NEW package
│   ├── __init__.py              ← load_default_pack(), merge_packs()
│   ├── _loader.py               ← markdown+YAML frontmatter parser
│   ├── explorer.md              ← pack member: read-only investigator
│   ├── planner.md               ← pack member: spec writer with scope-creep guard
│   ├── general_purpose.md       ← pack member: full-tool catch-all (no Bash, no Task)
│   └── README.md                ← Security: CLAUDE.md visibility section (SC-11)
├── sdk_teammate.py               ← MODIFIED: + agents kwarg, failure handling
└── factories.py                  ← MODIFIED: + agents pass-through

tests/
├── fakes/sdk.py                  ← MODIFIED: scripted_system_messages support
├── test_subagents.py             ← NEW: loader + merge + integration with FakeSDK
└── test_live_subagents.py        ← NEW: gated SC-10 live smoke
```

The pack is a self-contained subpackage. Three `.md` files plus a loader is the entire feature surface inside `claude_crew/`. Integration into `SdkTeammate` is a single new constructor kwarg + a `ClaudeAgentOptions(agents=...)` line in `_run()`. Failure handling lives in `_handle_one_turn`.

### Data / API Contracts

```python
# claude_crew/subagents/__init__.py

from claude_agent_sdk.types import AgentDefinition

PACK_MEMBERS: tuple[str, ...] = ("explorer", "planner", "general-purpose")

def load_default_pack() -> dict[str, AgentDefinition]:
    """Load the bundled pack from `claude_crew/subagents/*.md`.

    Returns a dict keyed by the kebab-case role name (matches Claude
    Code convention). Called fresh each invocation — no module cache —
    so file edits are picked up by the next teammate spawn within the
    same process.

    Raises:
        PackLoadError: if any required file is missing, frontmatter
            fails to parse, or a required field is absent.
    """

def merge_packs(
    default: dict[str, AgentDefinition],
    user: dict[str, AgentDefinition] | None,
) -> dict[str, AgentDefinition]:
    """Merge a user-defined agent dict over the default pack.

    Per Phase 1 contract: per-key override at the whole-AgentDefinition
    level. User wins on collision. Non-conflicting user keys are added.
    None or empty `user` returns `default` unchanged.
    """
```

```python
# claude_crew/subagents/_loader.py

@dataclass(frozen=True)
class PackFrontmatter:
    description: str
    model: str             # "haiku"|"sonnet"|"opus" or full id
    tools: list[str]
    effort: str | None = None         # "low"|"medium"|"high"|"max"
    maxTurns: int | None = None
    initialPrompt: str | None = None

def parse_pack_file(path: Path) -> tuple[str, AgentDefinition]:
    """Parse one `.md` file with YAML frontmatter + body.

    Returns (key, AgentDefinition). Key is the file stem with
    underscores converted to hyphens (`general_purpose.md` →
    `"general-purpose"`).

    Required frontmatter: `description`, `model`, `tools`.
    Optional: `effort`, `maxTurns`, `initialPrompt`.
    Body (everything after the closing `---`) is the AgentDefinition.prompt.

    Raises PackLoadError on any structural violation.
    """
```

```python
# claude_crew/sdk_teammate.py — modified __init__ signature

def __init__(
    self,
    id: str,
    name: str,
    role: str,
    *,
    model: str = "claude-sonnet-4-6",
    effort: str | None = None,
    system_prompt: str | None = None,
    setting_sources: list[str] | None = None,
    agents: dict[str, AgentDefinition] | None = None,   # ← NEW
) -> None:
    ...
    self._agents = agents if agents is not None else load_default_pack()
```

```python
# in _run(), at the existing options-construction line

opts_kwargs["agents"] = self._agents
options = ClaudeAgentOptions(**opts_kwargs)
```

```python
# claude_crew/factories.py — modified sdk_factory

def sdk_factory(
    id: str, name: str, role: str,
    *, model: str | None = None, effort: str | None = None,
    agents: dict[str, AgentDefinition] | None = None,    # ← NEW (SC-9 seam)
) -> SdkTeammate:
    kwargs = {}
    if model is not None: kwargs["model"] = model
    if effort is not None: kwargs["effort"] = effort
    if agents is not None: kwargs["agents"] = agents
    return SdkTeammate(id=id, name=name, role=role, **kwargs)
```

### Subagent Pack Files

Each file is YAML frontmatter + markdown body. The body is the system prompt; structure has three sections (`Role`, `Contract`, `Voice`) per co-architect's "contract" requirement.

**`claude_crew/subagents/explorer.md`** (sketch — Phase 4 finalizes prose):

```markdown
---
description: Read-only codebase investigator. Finds files, reads code, reports facts.
model: haiku
tools: [Read, Grep, Glob]
effort: low
maxTurns: 10
initialPrompt: Begin by stating what you're searching for and where you'll look. Then proceed.
---

# Role
You are an explorer. You find things in the codebase and report what you found —
file paths, line numbers, exact code excerpts, structural facts. You do not
write, edit, opine, or design.

# Contract
You MUST:
- Cite file paths with line numbers when reporting findings
- Quote code excerpts verbatim, not paraphrased
- Stop and report when you have an answer; do not branch into related
  investigations the caller didn't ask for
- Say "not found" plainly when something isn't there

You MUST NOT:
- Edit, write, or modify any file (your tool surface enforces this)
- Make architectural recommendations or refactor suggestions
- Speculate about author intent — report what is, not what might have been

# Voice
Terse. Structured. File:line precision. No prose padding.
```

**`claude_crew/subagents/planner.md`** (sketch):

```markdown
---
description: Spec writer. Restates the task, names acceptance criteria, produces a spec doc.
model: sonnet
tools: [Read, Grep, Glob, Write]
effort: high
maxTurns: 20
initialPrompt: Begin by restating the task in your own words and naming the acceptance criteria you will satisfy. Then proceed.
---

# Role
You are a planner. You take an ambiguous task, sharpen it into a spec, and
write that spec to a new file. You explore the codebase enough to ground the
plan in reality, then commit a document. You do not implement.

# Contract
You MUST:
- Restate the task and name acceptance criteria as your first action (the
  initialPrompt enforces this; do not skip it)
- Read code before specifying — designs that don't reflect the codebase are
  wrong by default
- Produce a spec doc via Write when the plan is complete
- Identify edge cases, failure paths, and validation boundaries explicitly

You MUST NOT:
- Edit existing files (your tool surface gives Write but not Edit; new
  artifacts only)
- Implement any part of the plan; specs are a handoff to a builder
- Make product decisions outside the spec's scope
- Hand-wave with "TODO" or "we'll figure it out later"
- Spawn subagents — you have no Task tool by design

# Voice
Direct. Opinions stated plainly. Trade-offs named, not hidden.
```

**`claude_crew/subagents/general_purpose.md`** (sketch):

```markdown
---
description: Catch-all assistant for shaped work — find, read, write, search, edit, fetch.
model: sonnet
tools: [Read, Grep, Glob, Edit, Write, WebFetch, WebSearch]
effort: medium
maxTurns: 20
---

# Role
You are general-purpose. You handle work that doesn't fit a specialized role —
research, drafting, light implementation, web lookup. You have access to a
broad tool surface but no shell.

# Contract
You MUST:
- Stay scoped to what was asked; if the task is unclear, ask one
  clarifying question and stop
- Cite sources when fetching from the web
- Use Edit for in-place changes and Write for new files
- Stop when the task is complete, not when turns run out — turn budget
  is a ceiling, not a target

You MUST NOT:
- Run shell commands (you have no Bash tool by design — do not ask the
  caller to give you one)
- Spawn subagents (you have no Task tool by design — subagents are leaves)
- Make scope or product decisions on the caller's behalf. Surface
  options and recommendations; let the caller pick.

# Voice
Adaptable to the task. Direct. Surface uncertainty rather than hide it.
```

The kebab-case key `"general-purpose"` is what the SDK uses to invoke; the file is `general_purpose.md` because filesystem hyphens are awkward.

### Failure Handling (SC-8)

The existing `_collect_response_text(client) -> str` already drains
`client.receive_response()` to completion. SC-8(a) needs the most recent
failed `TaskNotificationMessage` from that same stream, so we **augment
the helper** rather than add a parallel drainer:

```python
# claude_crew/sdk_teammate.py

@dataclass(frozen=True)
class TurnDrainResult:
    text: str
    last_failed_task_notif: TaskNotificationMessage | None

async def _collect_response_text(client: Any) -> TurnDrainResult:  # ← signature change
    """Drain client.receive_response().

    Returns a TurnDrainResult with:
      - text: concatenated TextBlock content from AssistantMessages (existing behavior)
      - last_failed_task_notif: the most recent TaskNotificationMessage with
        status in {"failed","stopped"}, or None if all subagent runs succeeded
        (or no subagent ran at all)

    Raises RateLimitedError on rejected RateLimitEvent (existing behavior).
    """
    text_parts: list[str] = []
    last_failed: TaskNotificationMessage | None = None
    async for msg in client.receive_response():
        if isinstance(msg, RateLimitEvent):
            # ... existing logic ...
            continue
        if isinstance(msg, TaskNotificationMessage):
            if msg.status in ("failed", "stopped"):
                last_failed = msg
                logger.warning(
                    "subagent failure: status=%s task_id=%s summary=%r",
                    msg.status, msg.task_id, msg.summary,
                )
            continue
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return TurnDrainResult(text="".join(text_parts), last_failed_task_notif=last_failed)
```

Single caller (`_handle_one_turn`) updates accordingly:

```python
result = await asyncio.wait_for(_collect_response_text(client), timeout=TURN_TIMEOUT_SECONDS)
text = result.text

if not text and result.last_failed_task_notif is not None:
    notif = result.last_failed_task_notif
    summary = notif.summary or "subagent run did not complete"
    await self._send_error_envelope(
        to=env.sender,
        code="invalid_response",
        message=f"subagent failed: {summary}",
    )
    return
# else: existing empty-text path → invalid_response with the existing message
```

Stream-level exceptions (SC-8(b)) flow through the existing `except Exception`
in `_handle_one_turn`. The only change: add a `logger.warning` before the
existing `_send_error_envelope` call so operators tailing stderr see it.

**Multiple-subagent-in-one-turn semantics:** `_collect_response_text` keeps
only the **most recent** failed task notification. If three subagents run
and #3 fails, `last_failed_task_notif` is #3's. If #2 failed but the parent
recovered with text after, `text` is non-empty and we deliver it normally —
the recovery is the parent's responsibility, and a non-empty parent reply
is the signal that recovery happened. The unconditional `logger.warning`
inside the drain loop fires regardless, so operators tailing stderr see the
failure even when the parent narrates over it. **Test coverage** — two cases
required in Phase 3 (load-bearing for the recovery contract):
  - **(α) failure-and-empty:** last subagent fails, parent text is empty →
    failure envelope synthesized from `summary`.
  - **(β) failure-and-recovery:** last subagent fails, parent recovers with
    text → normal envelope **AND** warning log captured (assert via
    `caplog`). Without (β), a refactor that only logs on
    no-recovery-and-failed silently regresses operator visibility.

Logger name: `logging.getLogger("claude_crew.sdk_teammate")` — already used
elsewhere in the module (Feature #2).

### Edge Cases

- **Pack file missing** — `load_default_pack()` raises `PackLoadError` with the missing path. Teammate spawn fails fast at `__init__`, broker reports the error to the lead via the existing spawn-failure path.
- **Frontmatter malformed** — same: `PackLoadError` with file + parse error.
- **Required frontmatter field absent** (e.g., no `tools`) — `PackLoadError` naming the field. We do NOT default to "all tools" or `None`; missing means broken.
- **Body empty** (frontmatter present, no prompt) — `PackLoadError`. The prompt is the whole point.
- **User pack contains a key not in `PACK_MEMBERS`** — fine, gets added (this is how #3b's loader extends the pack).
- **User pack contains a key in `PACK_MEMBERS`** — user wins, full replacement at AgentDefinition level (Phase 1 contract).
- **Empty user pack `{}`** — `merge_packs` returns `default` unchanged.
- **None user pack** — same as `{}`.
- **`SdkTeammate(agents={})`** — empty dict explicitly disables the pack for that teammate (Task tool will report no agents available). Distinct from `agents=None` which loads default. This is the SC-9 seam, used by tests.
- **`TaskNotificationMessage` arrives but stream doesn't terminate** — existing per-turn timeout (`TURN_TIMEOUT_SECONDS`) still applies. The notification is just a marker; we don't wait on it specifically.
- **Multiple subagent invocations within one turn** — each emits its own `TaskNotificationMessage`. Our `last_task_notif` tracks the most recent; if the *last* one failed but earlier ones succeeded, the synthesized envelope still reflects the failure (correct: the parent's overall reply is the empty/failed state). If the parent produced text after the failure, we deliver normally per existing text-non-empty path.
- **Parent text empty AND no `TaskNotificationMessage` seen** — falls through to existing `invalid_response` handling.
- **`agents` parameter contains an invalid `AgentDefinition`** — we don't validate at our layer; SDK will raise on init. Surfaces as the existing catastrophic-failure path in `_run()`.

### Validation Contracts at Handoff Boundaries

| Boundary | Preconditions | Failure Behavior | Postconditions |
|---|---|---|---|
| `load_default_pack()` → `SdkTeammate.__init__` | All three pack files exist and parse | `PackLoadError` | Returns dict with exactly `PACK_MEMBERS` keys, each a fully-populated `AgentDefinition` |
| `SdkTeammate.__init__` → `_run` | `self._agents` is dict (possibly empty) | n/a — pure assignment | `ClaudeAgentOptions(agents=self._agents)` succeeds |
| `_run` → SDK | SDK accepts `agents` dict shape | SDK raises during `__aenter__`; caught by existing catastrophic try/except, error envelope to lead | Subprocess running with pack registered |
| `_handle_one_turn` (stream drain) → lead envelope | All paths produce exactly one envelope | n/a | Lead always receives an envelope per inbox message |

### Cross-Feature Integration Check

- **`SdkTeammate.__init__`** — adding optional kwarg with `None` default is backward-compatible. All existing call sites (`tests/test_sdk_teammate.py`, `tests/test_server_sdk_mode.py`, `claude_crew/factories.py:sdk_factory`) keep working.
- **`sdk_factory`** — same. Optional kwarg.
- **`make_server` and broker** — unchanged; they pass kwargs through.
- **`FakeSDKClient`** — the existing `scripted_responses[idx]` list yields whatever message instances it contains. `TaskNotificationMessage` instances simply go into that list (before the terminal `ResultMessage`); no parallel parameter needed. Add a single helper to `tests/fakes/sdk.py`: `task_failure_response(summary: str, status: str = "failed")` returning a list of messages that ends in a `TaskNotificationMessage(status=...)` followed by a `ResultMessage`. Mirrors the existing `text_response()` builder. Backward-compatible.
- **Existing live tests** (`test_live_sdk.py`) — these exercise SdkTeammate without invoking subagents. They will now receive a teammate whose options carry the pack registration, which is harmless. Verified by reading the test bodies (no Task-tool assertions).
- **MCP tool surface** — unchanged. `spawn_teammate` does not gain an `agents` parameter in #3a (operator-facing override is OOS).

### Test Strategy (per `validate-before-change.md`)

| Layer | Target | File | Approach |
|---|---|---|---|
| Implementation | `parse_pack_file` (each happy + sad path) | `test_subagents.py` | Direct calls with synthetic `.md` strings via tmp_path |
| Implementation | `load_default_pack` (SC-4, SC-5, SC-7) | `test_subagents.py` | Calls into the real bundled pack; asserts keys, model values, prompt body literal-match |
| Implementation | `merge_packs` (Phase 1 override semantics) | `test_subagents.py` | Synthetic AgentDefinition dicts; assert user-wins, whole-replace, none-passthrough |
| Implementation | No-PARENT_MARKER leakage (SC-2 always-runs) | `test_subagents.py` | Construct ClaudeAgentOptions with parent system_prompt='PARENT_MARKER'; iterate options.agents values; assert no field carries it |
| Integration | SdkTeammate passes pack to ClaudeAgentOptions (SC-6) | `test_subagents.py` | Patch `claude_crew.sdk_teammate.ClaudeAgentOptions` with a recorder that captures kwargs; start the teammate; await one no-op turn; assert recorded `agents` equals the loaded pack. Use existing FakeSDKClient for the inbox loop. |
| Integration | Internal seam (SC-9) — custom dict and explicit empty | `test_subagents.py` | Two cases: (a) `SdkTeammate(agents=custom_dict)` recorded kwargs match `custom_dict`; (b) `SdkTeammate(agents={})` recorded kwargs are `{}` (distinct from `None`-→-default-pack). |
| Integration | SC-8(a) — graceful failure with empty parent text | `test_subagents.py` | Script FakeSDKClient: emit TaskNotificationMessage(status="failed", summary="X"), then ResultMessage with no text; assert envelope `code="invalid_response"`, message contains "X" |
| Integration | SC-8(b) — stream-level exception | `test_subagents.py` | Script FakeSDKClient: receive_response raises; assert envelope `code="internal"`, teammate `_task` still alive |
| Integration | MCP spawn → default pack present (SC-6) | `test_subagents.py` | In-process MCP harness spawns via tool; introspect SdkTeammate; assert pack keys |
| Doc | SC-11 security section | `test_subagents.py` | Read `claude_crew/subagents/README.md`; regex match `Security[: ].*CLAUDE\.md` |
| Live | SC-10 end-to-end smoke | `test_live_subagents.py` | Real ClaudeSDKClient; spawn teammate with default pack; invoke each subagent; assert each completes; tool-name check (planner uses Write, general-purpose uses WebFetch); isolation regression (CLAUDE.md visible, conversation NOT, system_prompt NOT) |

### Design Decisions

- **Loader: per-call, not module-cached.** Pack files are tiny (3 files, ~50 lines each); the perf cost of re-reading on every spawn is negligible. Determinism (SC-7) is cleaner without a cache, and operators who edit a `.md` file mid-process see the change on next spawn.
- **YAML frontmatter format.** Matches the convention in `~/.claude/agents/*.md` (Jerome's existing scout/feature-planner/builder/runner/sentinel agents use this). Feature #3b's loader can share the same parser.
- **`PyYAML` for parsing.** Standard, ubiquitous. Add to `pyproject.toml` deps. (Verified during build that it is not already a transitive dep — explicit add required.)
- **Kebab-case keys, snake_case filenames.** `general-purpose` is the Claude Code convention for the subagent name; filesystems and Python module names prefer snake_case. The loader maps `general_purpose.md` → `"general-purpose"` deterministically.
- **`agents={}` empty-dict semantics.** Distinct from `None`. Empty dict means "no pack, this teammate cannot delegate." Used by tests and by future operators who want a degraded-mode teammate. Documented in the kwarg's docstring.
- **No validation of user-supplied AgentDefinitions.** We trust the SDK to validate field shapes. Adding our own validation duplicates effort and creates two sources of truth.
- **Failure logging at WARNING.** Not ERROR (subagent failure is not a process-level error; the parent often handles it gracefully) and not INFO (operators tailing logs need to see it). Matches Python logging conventions for "something went wrong but we recovered."

### Assumptions (default-accept)

- **A1: PyYAML is acceptable as a runtime dep.** Already implicitly used by other Claude tooling; small, stable, well-known. *Default:* add to `pyproject.toml`.
- **A2: The SDK's `tools` allowlist accepts the literal strings `"Read"`, `"Grep"`, `"Glob"`, `"Edit"`, `"Write"`, `"WebFetch"`, `"WebSearch"`.** Verified for `"Read"` in spike; SC-10 verifies the rest. *Default:* assume yes; SC-10 catches if wrong.
- **A3: `model: "haiku"` and `model: "sonnet"` aliases route to current-generation models the SDK chooses.** We don't pin full model IDs. *Default:* trust the alias; revisit if dogfooding shows wrong-model behavior.
- **A4: Existing `_classify_error` will correctly classify SDK stream exceptions as `code="internal"`.** This was Feature #2's contract; #3a does not modify it. *Default:* reuse as-is.
- **A5: An empty `agents={}` dict is a valid value for `ClaudeAgentOptions.agents` and is treated distinctly from `None`.** SDK source declared the field as `dict[str, AgentDefinition] | None`; `{}` is a valid dict. The semantic distinction (`{}` = "this teammate has no pack" vs. `None` = "load default pack at our layer") lives entirely in `SdkTeammate.__init__`; the SDK only sees what we pass. *Default:* yes. *Verified by:* SC-9 case (b) — explicit `agents={}` test asserts the recorded `ClaudeAgentOptions.agents` is `{}`. If the SDK silently coerces `{}` to `None`, that test fails and we revisit; if it accepts `{}` and reports no agents to the model, we proceed.

### Open Questions

*None.* Phase 1 closed all Phase 1 questions; Phase 2 carryovers were design-not-decision and are now resolved above.

**Gate**:
- ✅ Design clear and justifiable
- ✅ Spec comprehensive — no ambiguity, no TODOs
- ✅ ALL edge cases listed
- ✅ Error handling specified
- ✅ Validation contracts at every handoff boundary (multi-stage features)
- ✅ Cross-feature integration check complete — verified by reading rendering/query code, not just naming files
- ✅ New architecture decisions captured in `.claude/rules/`
- ✅ Implementable by someone with no additional context

---

## Phase 3: Task Breakdown

Four tasks. Task 1 is the pack itself (no deps). Task 2 wires it into `SdkTeammate` (depends on 1). Task 3 adds the failure-handling for SC-8 (depends on 2). Task 4 is the gated live E2E covering SC-10 + isolation regression (depends on 1-3).

---

### Task 1: Pack package — files, loader, merge, security doc

**Depends on**: None | **Blocks**: Task 2, Task 4

**Scope:**
- New package `claude_crew/subagents/`:
  - `__init__.py` exporting `load_default_pack()`, `merge_packs()`, `PackLoadError`, `PACK_MEMBERS`
  - `_loader.py` with `parse_pack_file()` and the `PackFrontmatter` dataclass
  - `explorer.md`, `planner.md`, `general_purpose.md` — final prose per Phase 2 sketches with co-architect's edits applied
  - `README.md` containing the "Security: CLAUDE.md visibility" section (SC-11)
- `pyproject.toml` adds `pyyaml>=6.0`
- New test file `tests/test_subagents.py` with `TestPackLoader`, `TestMerge`, `TestPackContents`, `TestSecurityDoc` test classes

**Acceptance Criteria**:

```
Scenario: Loader returns the three pack members with correct shape (SC-4, SC-7)
  Given the bundled pack files on disk
  When load_default_pack() is called twice in succession
  Then both calls return the same dict with keys exactly {"explorer", "planner", "general-purpose"}
  And explorer's AgentDefinition has model="haiku" and tools=["Read","Grep","Glob"]
  And planner's AgentDefinition has model="sonnet" and tools=["Read","Grep","Glob","Write"]
  And general-purpose's AgentDefinition has model="sonnet" and tools that include "WebFetch" and exclude "Bash" and exclude "Task"

Scenario: Per-subagent budgets pinned in config (SC-3)
  Given the bundled pack files on disk
  When load_default_pack() is called
  Then explorer.maxTurns == 10
  And planner.maxTurns == 20 and planner.effort == "high"
  And general-purpose.maxTurns == 20 and general-purpose.effort == "medium"
  And explorer.effort == "low"

Scenario: Pack prompts are hermetic — body matches source file literally (SC-5)
  Given the bundled pack files on disk
  When load_default_pack() is called
  Then explorer.prompt equals the markdown body of explorer.md verbatim (whitespace-preserved)
  And the same holds for planner and general-purpose

Scenario: parse_pack_file fails loudly on missing required field
  Given a tmp .md file with frontmatter omitting "tools"
  When parse_pack_file is called on it
  Then PackLoadError is raised with a message that names "tools" and the file path

Scenario: parse_pack_file fails loudly on empty body
  Given a tmp .md file with valid frontmatter and empty body
  When parse_pack_file is called on it
  Then PackLoadError is raised with a message identifying the file

Scenario: merge_packs — user wins on collision at whole-AgentDefinition level
  Given a default pack with key "planner" mapping to AgentDefinition(model="sonnet", ...)
  And a user dict with key "planner" mapping to AgentDefinition(model="opus", maxTurns=5)
  When merge_packs(default, user) is called
  Then the result["planner"] is the user's full AgentDefinition (model="opus", maxTurns=5) — no field-merge

Scenario: merge_packs — user adds a non-conflicting key
  Given a default pack and a user dict containing only key "reviewer"
  When merge_packs is called
  Then the result has all default keys plus "reviewer"

Scenario: merge_packs — None or empty user passes through default
  When merge_packs(default, None) and merge_packs(default, {}) are called
  Then both return default unchanged (==, dict-equal)

Scenario: Security section is documented (SC-11)
  Given the file claude_crew/subagents/README.md
  When its contents are read
  Then a heading line matches the regex r"Security[: ].*CLAUDE\.md"
  And the section names "general-purpose" with WebFetch and WebSearch
  And the section recommends auditing CLAUDE.md before relying on the default pack
```

**Verification**:
```
uv run pytest tests/test_subagents.py -v
```
Fails before this task because `tests/test_subagents.py` and the `claude_crew.subagents` module don't exist.

---

### Task 2: SdkTeammate integration — `agents` kwarg + factory pass-through

**Depends on**: Task 1 | **Blocks**: Task 3, Task 4

**Scope:**
- `claude_crew/sdk_teammate.py`:
  - Add `agents: dict[str, AgentDefinition] | None = None` to `__init__`
  - Store as `self._agents = agents if agents is not None else load_default_pack()` (importing from `claude_crew.subagents`)
  - In `_run`, set `opts_kwargs["agents"] = self._agents` before constructing `ClaudeAgentOptions`
- `claude_crew/factories.py`:
  - Add `agents` kwarg to `sdk_factory`; thread through to `SdkTeammate`
- `tests/test_subagents.py` adds `TestSdkTeammateIntegration` class

**Acceptance Criteria**:

```
Scenario: Default-pack auto-registration on spawn (SC-6)
  Given a teammate spawned through the in-process MCP server with default args
  When ClaudeAgentOptions is constructed inside SdkTeammate._run
  Then the recorded options.agents has exactly the keys {"explorer","planner","general-purpose"}

Scenario: Internal override seam — custom dict wins (SC-9 case a)
  Given SdkTeammate(agents={"reviewer": <custom AgentDefinition>})
  When _run constructs ClaudeAgentOptions
  Then the recorded options.agents == {"reviewer": <custom AgentDefinition>}
  And the default pack is NOT loaded for this teammate

Scenario: Internal override seam — empty dict ≠ None (SC-9 case b, A5)
  Given SdkTeammate(agents={})
  When _run constructs ClaudeAgentOptions
  Then the recorded options.agents == {}  # explicit empty, not the default pack

Scenario: Parent system_prompt does not leak into any pack member (SC-2 always-runs)
  Given SdkTeammate(system_prompt="PARENT_MARKER_X9F2", agents=None)
  When _run constructs ClaudeAgentOptions
  Then no AgentDefinition value in options.agents has any field whose string representation contains "PARENT_MARKER_X9F2"

Scenario: Factory passes agents through to SdkTeammate
  Given sdk_factory("id-1","alice","planner", agents={"x": <agent>})
  When the resulting SdkTeammate is inspected
  Then teammate._agents == {"x": <agent>}
```

**Verification**:
```
uv run pytest tests/test_subagents.py::TestSdkTeammateIntegration -v
uv run pytest  # entire suite — must still pass; nothing regressed
```
Fails before this task because `SdkTeammate` does not accept `agents`.

---

### Task 3: SC-8 failure handling — augment drain, synthesize-from-summary, log

**Depends on**: Task 2 | **Blocks**: Task 4

**Scope:**
- `claude_crew/sdk_teammate.py`:
  - Add `TurnDrainResult` dataclass
  - Change `_collect_response_text` return type to `TurnDrainResult`; track `last_failed_task_notif`; emit `logger.warning` for every failed `TaskNotificationMessage`
  - In `_handle_one_turn`: on empty text + non-None `last_failed_task_notif`, synthesize error envelope from `summary` (or default message)
  - On stream-level exception (existing path), add `logger.warning` before the existing error-envelope send
- `tests/fakes/sdk.py`:
  - Add `task_failure_response(summary: str, status: str = "failed") -> list[Message]` builder (mirrors existing `text_response()`)
  - Document that `TaskNotificationMessage` instances go directly into `scripted_responses[idx]` lists
- `tests/test_subagents.py` adds `TestFailureHandling` class

**Acceptance Criteria**:

```
Scenario: SC-8(a) — graceful failure with empty parent text
  Given a FakeSDKClient scripted to emit TaskNotificationMessage(status="failed", summary="ran out of turns") then a ResultMessage with no AssistantMessage text
  When SdkTeammate processes one inbox envelope
  Then the lead receives an envelope with payload.error == "invalid_response"
  And payload.message contains "ran out of turns"
  And the teammate's worker task is still running

Scenario: SC-8(a) — graceful failure with empty summary
  Given a TaskNotificationMessage with status="failed" and summary=None or ""
  When SdkTeammate processes the turn with empty parent text
  Then the lead receives an envelope with payload.error == "invalid_response"
  And payload.message contains the default text "subagent run did not complete" (or equivalent)

Scenario: SC-8(b) — stream-level exception
  Given a FakeSDKClient whose receive_response raises a generic Exception mid-stream
  When SdkTeammate processes one inbox envelope
  Then the lead receives an envelope with payload.error == "internal"
  And the teammate's worker task is still running
  And a WARNING log line was emitted naming the teammate id

Scenario: Multi-subagent (α) — last fails, parent text empty
  Given two TaskNotificationMessages, the second with status="failed" summary="bad"
  And no AssistantMessage text follows
  When SdkTeammate processes the turn
  Then the synthesized envelope reflects the LAST failure ("bad")

Scenario: Multi-subagent (β) — last fails, parent recovers with text
  Given a TaskNotificationMessage status="failed" summary="bad", followed by an AssistantMessage with text="ok, here's the answer"
  When SdkTeammate processes the turn (caplog enabled)
  Then the lead receives a NORMAL envelope with text "ok, here's the answer" (recovery wins)
  And caplog contains a WARNING log mentioning the failure (operator visibility preserved)

Scenario: Successful turn — no warnings, no failure synthesis
  Given a normal text response with no TaskNotificationMessage
  When SdkTeammate processes the turn
  Then the lead receives a normal envelope with the text
  And caplog contains no WARNING entries from the sdk_teammate logger
```

**Verification**:
```
uv run pytest tests/test_subagents.py::TestFailureHandling -v
uv run pytest  # entire suite — Feature #2's existing tests (which call _collect_response_text indirectly) must still pass
```
Fails before this task because the synthesis path doesn't exist; multi-subagent (β) specifically fails because the WARNING log isn't emitted on recovery.

---

### Task 4: End-to-end live smoke + isolation regression (SC-10)

**Depends on**: Tasks 1, 2, 3 | **Blocks**: None

**Scope:**
- New file `tests/test_live_subagents.py` — module-level `pytestmark = pytest.mark.skipif(...CLAUDE_CREW_LIVE_TESTS != "1"...)`
- Spawns a real teammate via the in-process MCP harness (or directly via `SdkTeammate`) using the default pack
- Drives one turn per subagent, captures `TaskNotificationMessage` results
- Cost expectation: ~$0.40 per run

**Happy Path Scenarios**:

```
Scenario: Each pack member completes a basic invocation (SC-1, SC-10)
  Given a real teammate spawned with the default pack
  When the lead sends three messages, each asking the teammate to delegate to one of {explorer, planner, general-purpose}
  Then each TaskNotificationMessage observed has status="completed"
  And the lead receives one envelope per message containing non-empty text

Scenario: Tool-name correctness — non-Read tools are accepted by the SDK (SC-1)
  Given the teammate has invoked planner on a "produce a tiny spec doc" prompt
  Then at least one tool-use observed in the parent stream is "Write"
  Given the teammate has invoked general-purpose on a "look up X on the web and report" prompt
  Then at least one tool-use observed is "WebFetch" or "WebSearch"

Scenario: Isolation regression — CLAUDE.md visible (intentional)
  Given a real teammate is asked to invoke explorer with the prompt "Quote the user's name from CLAUDE.md or say 'none'"
  Then the subagent's reply contains "Jerome" (or "Kael") — confirms expected inheritance

Scenario: Isolation regression — parent conversation NOT visible
  Given the lead sends turn 1 planting a UUID into the teammate's conversation
  And turn 2 invokes explorer asking it to repeat the UUID or say "none"
  Then the explorer's reply is "none" (or otherwise does not contain the UUID)

Scenario: Isolation regression — parent system_prompt NOT visible
  Given the teammate is spawned with system_prompt="PARENT_MARKER_LIVE_3a"
  When explorer is invoked and asked to quote its own system prompt
  Then the quoted prompt does NOT contain "PARENT_MARKER_LIVE_3a"
  And it DOES contain wording from explorer.md (proving explorer's own prompt is in effect)
```

**Sad Path Scenarios** (live):

```
Scenario: Subagent task notification arrives even when the model declines
  Given a real teammate is asked to invoke planner with an impossible-to-satisfy prompt that requires denied tools
  When the planner runs to completion
  Then a TaskNotificationMessage is observed in the parent stream
  And its status is one of {"completed","failed","stopped"} (not absent)
```

(Forced-failure live tests are deliberately limited — the failure-handling unit/integration tests in Task 3 carry the heavier burden because they're cheap and deterministic.)

**Verification**:
```
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_subagents.py -v
```
Fails before this task because the file does not exist; cannot pass without Tasks 1-3 in place because the pack must load and integrate before the smoke can run.

---

**Gate**:
- ✅ 4 tasks, each independently testable
- ✅ Dedicated E2E test task with happy and sad path coverage
- ✅ Verification commands fail without the feature
- ✅ Each Phase 2 edge case traces to at least one BDD scenario — no edge case without a test
- ✅ Each Phase 2 cross-feature interaction has at least one scenario covering the consumer's behavior
- ✅ User approved

---

## Phase 4: Implementation

### Tasks shipped

| | Task | Commit | Tests added | SCs covered |
|---|---|---|---|---|
| T1 | Pack package + loader + .md files + security doc | `4bb65fd` | 22 | SC-3, SC-4, SC-5, SC-7, SC-11 |
| T2 | SdkTeammate integration (`agents` kwarg + factory) | `e85fb87` | 6 | SC-2 (always-runs), SC-6, SC-9 (a)+(b) |
| T3 | SC-8 failure handling (`TurnDrainResult` + synthesis + WARNING) | `34f8748` | 6 | SC-8 (a)+(b), multi-subagent (α)+(β) |
| Sentinel fixes | `background=False` enforced, SC-11 tests tightened, hygiene | `637a8fb` | 3 | (sharpening) |
| T4 | Live E2E — tool-name correctness + isolation regression | `1c5636c` | 1 | SC-1, SC-10 |

### Sentinel pass (autonomous, post-T3)

Five fix-now items folded into `637a8fb`:
- `background=False` was lost between Phase 1 contract and loader output (silent contract drift); loader + frontmatter + each pack file now set it explicitly + a regression test asserts it.
- SC-11 README test was too loose: `WebFetch or WebSearch` → `WebFetch and WebSearch`; added an assertion that `setting_sources` inheritance mechanism is named.
- `merge_packs` returned the same default object on no-op (potential future-mutation footgun for #3b); now always returns a fresh dict; added a mutation-isolation test.
- Lazy import of `load_default_pack` in `SdkTeammate.__init__` violated `feedback_lazy_imports.md` (project rule). Moved to module scope.
- Test hygiene: imports consolidated at top of file; renamed `_task_notification` → `task_notification` (was being imported across modules); removed dead `_EXPLICIT_EMPTY` constant.

### Live E2E note

First live run (`52s`, ~$0.30 actual) failed on the conversation-isolation probe — but the failure was a test-design flaw, not an implementation bug. The probe embedded the UUID in the subagent's own prompt, so the subagent saw it in its input and repeated it. Reworked the probe to plant a secret in the parent's T1 and ask the subagent in T2 *without* including the answer — second run passed in 53s.

Captured in T4's commit message so the lesson sticks for future live tests against the SDK.

**Gate**: All tasks complete; Sentinel review done; full suite green (156 + 1 live).

---

## Phase 5: Completion

### Verification
- [ ] Feature works against Phase 1 success criteria
- [ ] No regressions — full test suite passes
- [ ] Spec updated to match implementation
- [ ] Docs updated if user-facing behavior changed

### Retrospective

**What went well**:

**What was friction**:

**Improvements**:
1. [Specific, actionable change to workflow]

**Workflow updates made**:
- [ ] TEMPLATE.md or SKILL.md updated
- [ ] Project knowledge base updated (`.claude/rules/`)
- [ ] MEMORY.md updated (if cross-project insight)

**Gate**: Feature verified, retrospective captured, workflow improved.
