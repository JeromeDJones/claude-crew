## Task

Produce a combined spec+breakout artifact for the following idea. Write it to:
`/home/jerome/dev/claude-crew/.rr-worktrees/per-teammate-model-routing/.rr/specs/per-teammate-model-routing.md`

The artifact must conform to the schema in `doc/templates/spec-template.md` (read it once before
writing — it lives in the plugin install alongside this prompt). It must include all required
spec sections AND a `## Task Breakout` section with a fenced ```yaml tasks block that decomposes
the spec into a buildable task DAG. One artifact, one file, one review gate.

## Idea

Implement per-teammate model/base_url routing for claude-crew so a crew can run
SOME teammates against a local model (via claude-code-router / ccr) while the lead
and other teammates stay on the Anthropic API. Today routing is all-or-nothing per
crew process: SDK teammates inherit the parent process os.environ, so there is no
way to route only some teammates to a local backend.

READ THIS FIRST: doc/ideas/local-model-backed-teammates.md — the full idea brief.
It contains the validated foundation, the integration seam, the resolved SDK
finding, and the proposed work (where / tests / size).

CORE DELIVERABLE (load-bearing): a per-teammate `env` override threaded from spawn
time through to the SDK options.
- spawn_teammate gains an OPTIONAL env override (a dict of env-var name->value).
- Thread it through broker.py (the spawn_teammate factory signature already carries
  model/effort/cwd) and factories.py into sdk_teammate.py's
  ClaudeAgentOptions(env=...).
- VERIFIED already: claude-agent-sdk's ClaudeAgentOptions.env reaches the CLI
  subprocess and overrides inherited env on conflict (types.py:1475;
  subprocess_cli.py ~lines 406-456). No subprocess-mode path is needed.
- NON-REGRESSION: the inherited-env default (no override supplied) must behave
  exactly as today — teammates spawned without an env override are unchanged.

ALSO IN SCOPE (build it): a thin "local backend" preset/convenience that
encapsulates the three env vars an operator would otherwise have to remember:
ANTHROPIC_BASE_URL (ccr endpoint), a non-empty ANTHROPIC_API_KEY, and crucially
CLAUDE_CODE_ATTRIBUTION_HEADER=0 (the rotating telemetry hash that otherwise busts
local prompt caching). This recipe is already proven working against the Claude
Code CLI, so the preset is grounded, not speculative. Build it as a documented,
tested convenience that expands to an env override and rides on top of the generic
mechanism (so spawn_teammate can take either a raw env dict OR the local-backend
preset). Keep the generic env override as the underlying primitive — the preset is
a named bundle, not a parallel code path.

CONSTRAINTS / NON-NEGOTIABLES:
- Two-layer tests (house rule): broker layer (override propagates through
  spawn_teammate to the factory) AND sdk_teammate layer (override reaches
  ClaudeAgentOptions; inherited-env default not regressed). Happy + sad paths at
  both. Use stub mode (conftest default); do NOT require live SDK calls.
- Follow CLAUDE.md conventions: imports at module top, asyncio.get_running_loop(),
  bound async-iterator drains, no Windows CRLF in frontmatter, etc.
- Minimal and SRP. This is a thread-through of one optional parameter (plus the
  named preset on top), not a subsystem.
- HARD GATE — NO LIVE LOCAL MODEL: do NOT spin up llama.cpp / ccr / any local
  model server during this slice. ALL validation and tests run in STUB MODE
  (conftest default) with no live SDK or network calls. The local-backend preset
  is verified by asserting it expands to the correct env dict (the three vars) and
  that the override reaches ClaudeAgentOptions — NOT by launching a real backend.
  If anyone believes a live end-to-end check against a local model is needed, STOP
  and escalate to the operator (Jerome) first; he controls when the GPU model runs.

OUT OF SCOPE (do NOT spend cycles here):
- Mixed-routing UI / dashboard surfacing of which teammate uses which backend.
- ccr config management or the llama.cpp/ccr setup itself (operator infra).
- Multiple concurrent local servers / ccr provider management.
- Per-teammate model-NAME routing via ccr (the model string is cosmetic under ccr
  today; this feature is about env/base_url, not model selection).

POINTERS THE PLANNER SHOULD READ:
- doc/ideas/local-model-backed-teammates.md (the brief)
- claude_crew/sdk_teammate.py (ClaudeAgentOptions construction, ~line 1221)
- claude_crew/broker.py (spawn_teammate factory signature, ~lines 132 and 191)
- claude_crew/factories.py (resolved_model/effort threading, ~lines 176 and 336)
- claude_crew/server.py (the spawn_teammate MCP tool surface the lead calls)
- claude_crew/auth.py (the credential gate; a non-empty key satisfies it)
- CLAUDE.md (test conventions + verified SDK invariants)

## Cycle

Cycle: 0
Prior review report (empty on cycle 0): 

On cycle ≥ 1, read the prior report first. Address every Critical and High finding by name in the revised spec. Medium and Low findings are advisory.

## Repository Context

Repository path: `/home/jerome/dev/claude-crew`

Gather context before writing the spec:
- Read the repository README.
- Scan the top-level directory layout.
- Check `.rr/specs/` for prior specs (if any exist, avoid duplicating their scope).

### Reference Artifacts

Spec template (read before writing — includes Task Breakout schema in comments):
`/home/jerome/.claude/plugins/cache/repo-reactor/repo-reactor/0.7.7/doc/templates/spec-template.md`

Existing specs in this worktree:
_None._

### Architecture Context

Architecture doc: `(absent)`

If the path is `(absent)`, no architecture doc has been authored for this repo —
note that in your reasoning rather than failing. If the path resolves to a file,
read it before producing your spec; align your spec with the architecture it
describes, and call out any contradictions explicitly.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/per-teammate-model-routing`

Change to this directory before all file operations.

## Instructions

- The artifact must include all required spec sections: `## Problem`, `## Design Decisions`, `## Edge Cases`, `## Acceptance Tests`, `## Test Command`, `## Out of Scope`, `## Assumptions`, `## Open Questions`, `## Validation`, AND `## Task Breakout`.
- `## Test Command` must contain a non-empty `bash` or `sh` fenced code block with a runnable command.
- `## Task Breakout` must contain a fenced ```yaml block with a `tasks:` list. Every numbered acceptance test must be claimed by exactly one task. Each task needs `name`, `description`, `dependsOn`, `acceptanceTests`, `taskTouches`, and `implementationKind`.
- Scope to the smallest deliverable that satisfies the idea. Defer anything not required.
- Before finalizing `## Test Command`, you **must** be able to name every package the test files import. Cross-check each against the project's dependency manifest (`pyproject.toml`, `package.json`, `Cargo.toml`, etc.). If any import is not in the manifest — or if the tests require system-level setup (browser binaries, running services, env vars, compiled extensions) — state the prerequisite install command in prose above the fenced block. "No prerequisites" is only valid if you have confirmed every import is already in the manifest.
- Run `bin/spec-schema-check.sh` on the artifact before finalizing. Fix every reported gap.
- Write the combined artifact file only. Do not implement any code.
