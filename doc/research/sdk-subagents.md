# Claude Agent SDK — Subagent Behavior

**Created:** 2026-04-25
**Source:** Feature #3a Phase 1 spike. Source-reading of `claude_agent_sdk` v0.1.68
plus two live runs of `scripts/sdk_subagent_spike.py` against the real API.

This doc resolves the two verification items the product vision flagged as
gating Feature #3a (subagent context isolation, per-subagent token budgets)
and answers a third question (subagent observability surface) that was
needed to size Feature #4.

---

## SDK shape (source-read)

`AgentDefinition` (from `claude_agent_sdk/types.py:82`):

```python
@dataclass
class AgentDefinition:
    description: str
    prompt: str
    tools: list[str] | None = None
    disallowedTools: list[str] | None = None
    model: str | None = None                    # "sonnet" | "opus" | "haiku" | "inherit" | full id
    skills: list[str] | None = None
    memory: Literal["user", "project", "local"] | None = None
    mcpServers: list[str | dict[str, Any]] | None = None
    initialPrompt: str | None = None
    maxTurns: int | None = None
    background: bool | None = None
    effort: Literal["low", "medium", "high", "max"] | int | None = None
    permissionMode: PermissionMode | None = None
```

Wired to the parent via `ClaudeAgentOptions.agents: dict[str, AgentDefinition]`.
Names are the keys; the model invokes `Task(subagent_type="<name>", prompt="...")`
and the SDK runs the matching `AgentDefinition`'s loop in a child agent context
within the same subprocess.

Notably absent on `AgentDefinition`: `setting_sources`, `system_prompt`
(name-style), `cwd`. These are parent-level concerns.

---

## 1. Context isolation

### What leaks from parent into subagent

- **CLAUDE.md / setting_sources content.** ✗ leaks. The subagent inherits the
  parent's `setting_sources`. With `setting_sources=["user","project"]`, the
  subagent answered "Jerome" when asked to quote a name from any CLAUDE.md
  it could see — `~/.claude/CLAUDE.md` reaches the subagent.
- **Working directory (`cwd`).** ✗ leaks. The subagent's `pwd` returned
  `/home/jerome/dev/claude-crew` — same cwd as the parent.

### What does NOT leak

- **Parent's conversation history.** ✓ isolated. We planted a UUID
  (`uuid-isolation-7f3b9a2e1c4d`) in the parent's turn 1, then on turn 2 had
  the parent invoke the subagent and ask it to repeat the UUID. The subagent
  replied `none`. Subagents do not see prior parent conversation.
- **Parent's `system_prompt`.** ✓ isolated. The parent's
  `system_prompt="You are the parent for an isolation probe..."` did not
  reach the subagent. When asked to quote its system prompt, the subagent
  reported a boilerplate prefix (`"You are a Claude agent, built on
  Anthropic's Claude Agent SDK."`) followed by its own
  `AgentDefinition.prompt` — nothing from the parent.

### Implications for Feature #3a

- **`setting_sources` is a parent-level lever and there is no per-subagent
  override at the SDK level.** A teammate that loads CLAUDE.md for itself
  *also* loads it for every subagent it spawns. To strip CLAUDE.md from
  subagents, you would have to strip it from the parent too — which we don't
  want, because the teammate is supposed to feel like a collaborator that
  knows standing instructions. **Decision shape:** accept CLAUDE.md
  inheritance as the design default; document it; do not pretend it can be
  walled off.
- **Conversation isolation is real.** Subagents are stateless across
  invocations. Each Task call starts a fresh agent loop. No cross-talk.
- **System prompt is the per-subagent lever that works.** Whatever we put in
  `AgentDefinition.prompt` is the subagent's own contract. The parent's
  prompt does not bleed in.

---

## 2. Per-subagent token budgets

### What the SDK provides

`AgentDefinition` has `maxTurns: int | None` and `effort` (literal:
`"low"|"medium"|"high"|"max"` or `int`). Both are per-agent at the type
level — not session-wide on the parent.

### What we verified

- **`maxTurns` is enforced per-subagent.** A subagent configured with
  `maxTurns=1` and `tools=["Read"]` was asked to "read /etc/hostname, then
  in a second message summarize." The subagent used its one turn to call
  `Read`, was cut off, and returned only the pre-tool narration ("I'll read
  the hostname file for you"). `usage.tool_uses == 1` in the
  `TaskNotificationMessage`. No second turn occurred.
- **`tools` allowlist is enforced.** Same probe — the subagent had only
  `Read` allowed; only `Read` was called. The default tool set (Bash, Edit,
  etc.) was unavailable to that subagent.

We did not separately verify `effort` per-subagent in this spike (it
controls thinking-token depth, which is opaque from the outside). The type
system pins it as per-agent and we have no evidence to doubt that.

### Implications for Feature #3a

- **Per-subagent budgets work.** Planner can have a generous `maxTurns` /
  `effort` without starving the parent. Explorer can be capped. We can
  configure each pack member independently.
- **`tools` and `disallowedTools` are real, enforced sandboxes per
  subagent.** This is the lever for "explorer is read-only" and "planner
  can write its spec doc." The behavioral contract documented in each pack
  member's prompt is backed by an enforced tool surface — not honor system.

---

## 3. Subagent observability surface

### What the parent's `receive_response()` stream sees during a subagent run

In every probe run, the parent's stream emitted (in addition to its own
`AssistantMessage`s and `UserMessage`s):

- **`TaskStartedMessage`** (SystemMessage subtype `"task_started"`) — fields:
  `task_id`, `description`, `prompt` (the prompt the parent sent to the
  subagent), `session_id`, `tool_use_id`, `task_type`, `uuid`.
- **`TaskProgressMessage`** (SystemMessage subtype `"task_progress"`) —
  fires during the subagent's loop with running `usage` stats
  (`total_tokens`, `tool_uses`, `duration_ms`) and `last_tool_name`.
- **`TaskNotificationMessage`** (SystemMessage subtype `"task_notification"`)
  — fires on completion with `status` (`completed|failed|stopped`),
  `summary`, `output_file`, final `usage`, `task_id`, `tool_use_id`.
- **Subagent inner-loop messages bleed into the parent's stream**:
  - `AssistantMessage` from the subagent's loop arrives with
    `parent_tool_use_id` set to the parent's `Task` tool use id. (In our
    runs these arrived with no text content — text-only subagent answers
    appear to be delivered via the Task tool result rather than as
    standalone assistant text.)
  - `UserMessage` with `parent_tool_use_id` set carries tool results
    delivered *into* the subagent's loop (e.g., the subagent's `Read`/`Bash`
    output).

### Implications for Feature #4

The transcript scope today is lead↔teammate only. Widening it to include
subagent activity is **not architecturally bigger** because of this surface:

- A `TaskStartedMessage` is enough to log "teammate-X spawned subagent
  task-Y with prompt Z."
- A `TaskNotificationMessage` is enough to log "task-Y completed, used
  N tokens, M tool calls, took T ms, status `completed`."
- For finer-grained tool-use lineage, `parent_tool_use_id` on subagent
  messages is the join key.

This is a Feature #4-shaped concern, not a #3a one. We commit to the
finding here; we do not implement transcript widening as part of #3a.

---

## 4. What about `memory`?

`AgentDefinition.memory` is `Literal["user","project","local"] | None`. From
Feature #2 research (`sdk-memory.md`) we know the SDK does not activate the
auto-memory subsystem at all, so this field is currently a no-op for our
deployment. Worth re-probing if Anthropic ships memory activation in a
future SDK.

---

## Summary table

| Question | Answer | Source |
|---|---|---|
| Subagent sees parent's CLAUDE.md? | **Yes, via inherited `setting_sources`** | live probe |
| Subagent sees parent's conversation history? | No | live probe (UUID test) |
| Subagent sees parent's `system_prompt`? | No | live probe (Q4 system-prompt quote) |
| Subagent shares parent's cwd? | Yes | live probe (`pwd`) |
| `AgentDefinition.maxTurns` per-subagent? | **Yes, enforced** | live probe (truncation observed) |
| `AgentDefinition.tools` allowlist per-subagent? | **Yes, enforced** | live probe |
| `AgentDefinition.effort` per-subagent? | Per type system; not separately verified | source-read |
| Parent stream sees subagent activity? | **Yes — Task* SystemMessages + parent_tool_use_id markers** | live probe |
| `setting_sources` overridable per-subagent? | No (not on `AgentDefinition`) | source-read |

---

## Spike artifacts

- `scripts/sdk_subagent_spike.py` — the probe script. Gated on
  `CLAUDE_CREW_LIVE_TESTS=1`. ~$0.10 per run.
- Two runs were sufficient (one initial; one after sharpening the
  isolation probe with a child marker prefix and capturing tool-result
  payloads).
