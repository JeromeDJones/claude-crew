<!-- vars: SLUG, SPEC_BODY, WHAT_SHIPPED, FEATURE_REVIEW_SUMMARY,
     RETRO_FINDINGS, BACKLOG_DELTAS, VALIDATION_SUMMARY, COMMITS -->
<!-- Rendered by bin/extract-feature-doc.sh via envsubst at signoff.
     Written to doc/features/FEATURE-<slug>.md in the slice worktree.
     This template is the canonical condensed audit record for a shipped
     feature. All ${VAR} placeholders are substituted by the extractor;
     literal dollar signs in content must be escaped as $$ in source. -->

# Feature: multi-scope-agent-memory

## Spec

<!-- Full verbatim spec body extracted from .rr/specs/<slug>.md.
     Populated by SPEC_BODY. -->

# Spec: multi-scope-agent-memory

## Problem

claude-crew's per-role agent memory feature shipped with three scopes parsed
in the pack frontmatter (`user`, `project`, `local`) but only `user` actually
functions. When an operator declares `memory: project` or `memory: local`,
`sdk_teammate.py:501-506` emits a one-line WARN and silently skips injection
— the agent receives no memory scaffolding, no path guidance, no Write tool
auto-attach. Operators wanting committed/team-shared role learnings or
machine-local scratch memory have no working option. This spec finishes the
deferred v1 cut so all three scopes resolve to a real directory, carry
scope-appropriate write guidance, and auto-grant `Write` so persistence is
actually possible.

## Architecture Overview

Three modules participate. `claude_crew/teammate_memory.py` owns path
resolution and prompt-section construction (pure / I/O-light, no SDK
coupling). `claude_crew/sdk_teammate.py` is the injection point: at
`__init__` it reads `role_def.memory`, computes `_memory_section`, and
passes it to `build_teammate_prompt`. `claude_crew/factories.py` constructs
the per-spawn patched `AgentDefinition` (already does dataclasses.replace
for `extra_tools` / `extra_skills`) — the Write auto-attach rides this
existing seam OR happens at the sdk_teammate boundary; this spec picks the
sdk_teammate boundary so factories.py stays focused on operator-supplied
extras and the memory-driven mutation lives next to the rest of the memory
plumbing.

Project root for `project` / `local` scopes resolves from
`SdkTeammate._cwd` (set from the `cwd` kwarg in `__init__`). When `cwd` is
None (operator did not pass one), the scope cannot be resolved — the
implementation falls back to `Path.cwd()` and the resulting path will live
under the process's CWD; an Assumption below names this default.

### Call-site survey

`build_memory_section()` has exactly one caller (sdk_teammate.py:516).
`memory_dir()` has two callers (teammate_memory.py internals +
write_guard_deny_message). Single-shape; no divergence to resolve.

## Data / API Contracts

```python
# teammate_memory.py — new signatures

Scope = Literal["user", "project", "local"]

def memory_dir(role: str, scope: Scope = "user", project_root: Path | None = None) -> Path:
    """Return the role's memory directory for the given scope.

    user    -> Path.home() / ".claude" / "agent-memory" / <role>
    project -> project_root / ".claude" / "agent-memory" / <role>
    local   -> project_root / ".claude" / "agent-memory.local" / <role>

    Raises ValueError if scope is "project" or "local" and project_root is None.
    """

def memory_index_path(role: str, scope: Scope = "user", project_root: Path | None = None) -> Path:
    """Return the MEMORY.md path for the role/scope. Pure — no I/O."""

def build_memory_section(
    role: str,
    tools: tuple[str, ...] | None,
    scope: Scope = "user",
    project_root: Path | None = None,
) -> str:
    """Build the memory addendum. Picks scope-specific guidance text."""
```

Default values keep all existing `user`-scope callers source-compatible.

```python
# sdk_teammate.py — replace the WARN branch (lines 500-506) with:

if role_memory in ("user", "project", "local"):
    from claude_crew.teammate_memory import build_memory_section, ensure_write_tool

    project_root = Path(self._cwd) if self._cwd else Path.cwd()
    try:
        _memory_section = build_memory_section(
            role,
            getattr(role_def, "tools", None),
            scope=role_memory,
            project_root=project_root,
        )
    except ValueError:
        # unsafe role name — log and skip injection
        ...

    # Auto-attach Write to this role's AgentDefinition if memory is set
    # and Write is not already declared.
    patched_def = ensure_write_tool(role_def)
    if patched_def is not role_def:
        self._agents = {**self._agents, role: patched_def}
```

```python
# teammate_memory.py — new helper

def ensure_write_tool(agent_def: AgentDefinition) -> AgentDefinition:
    """Return agent_def unchanged if Write is in tools; else a dataclasses.replace
    copy with Write appended. Never mutates the input."""
```

## Design Decisions

- **Branch `memory_dir()` on a `scope` keyword arg defaulting to `"user"`** —
  *Rationale:* preserves the existing one-positional-arg API used by every
  test and by `write_guard_deny_message`; new scopes opt in by passing
  `scope=`. — *Carried into:* `memory_dir(role, scope, project_root)` signature
  and `test_memory_dir_branches_by_scope`.

- **Path layouts: `<root>/.claude/agent-memory/<role>/` for project,
  `<root>/.claude/agent-memory.local/<role>/` for local** — *Rationale:*
  `.claude/agent-memory/` mirrors the user-scope name so operators
  recognize the convention; `.local` suffix is the established gitignore
  convention (mirrors `.env.local`, `settings.local.json`). Two siblings
  at the same depth means a single recursive gitignore rule can pin
  `agent-memory.local/`. — *Carried into:* path assertions in
  `test_memory_dir_project_scope`, `test_memory_dir_local_scope`.

- **Project root = teammate's spawn-time `self._cwd`; fall back to
  `Path.cwd()` when None** — *Rationale:* claude-crew teammates already
  receive `cwd` per spawn (used by SDK + redaction); reusing it keeps the
  feature working out of the box. The fallback prevents a hard crash when
  legacy spawn paths omit `cwd`. — *Carried into:* the sdk_teammate
  injection block and `test_project_root_resolves_from_cwd`.

- **Per-scope guidance text via three module-level template constants,
  selected inside `build_memory_section`** — *Rationale:* the three scopes
  have qualitatively different "what to save / what NOT to save" rules
  (cross-project vs. project-shared vs. machine-local); a single template
  with conditionals would be harder to audit than three named blocks.
  — *Carried into:* `_INSTRUCTIONS_HEADER_USER`,
  `_INSTRUCTIONS_HEADER_PROJECT`, `_INSTRUCTIONS_HEADER_LOCAL` constants;
  asserted by `test_guidance_text_per_scope`.

- **Write auto-attach happens in `sdk_teammate.__init__` via
  `ensure_write_tool(role_def)` → `dataclasses.replace`** — *Rationale:*
  memory is the trigger and sdk_teammate is where memory wiring lives;
  factories.py stays focused on operator-supplied extras. Never mutate
  `self._agents[role]` in place — replace with a fresh dict so the shared
  default pack remains untouched (matches the F3b invariant). — *Carried
  into:* `ensure_write_tool()` and `test_write_tool_auto_attached`.

- **Local-scope guidance recommends a `.gitignore` entry but does NOT
  auto-edit `.gitignore`** — *Rationale:* explicit user constraint;
  silently mutating repo state is too surprising for a memory-write
  helper. — *Carried into:* `_INSTRUCTIONS_HEADER_LOCAL` string content;
  asserted by `test_local_guidance_mentions_gitignore`.

- **Verify non-collision with `is_lead_project_memory_path`** — the lead
  write-guard checks for `~/.claude/projects/<slug>/memory/**`. New paths
  live under `~/.claude/agent-memory/` (user) and `<root>/.claude/agent-memory{,.local}/`
  (project/local) — neither matches the protected `projects/<slug>/memory`
  shape. No code change to the guard; a regression test asserts the
  non-collision. — *Carried into:* `test_new_scopes_not_blocked_by_lead_guard`.

## Edge Cases

- `cwd=None` at spawn time → fall back to `Path.cwd()`; teammate gets
  whatever the running process's CWD resolves to. Logged at DEBUG.
- `role_def.memory == "project"` but `role_def.tools` already includes
  `"Write"` → `ensure_write_tool` returns the input unchanged
  (identity-comparison-safe).
- `role_def.tools` is `None` (pack omitted `tools:`) → `ensure_write_tool`
  treats it as empty, returns a replace with `tools=("Write",)`.
- `role_def.tools` is an empty tuple `()` (safe-by-default) → auto-attach
  produces `("Write",)`. The safe-by-default invariant is respected: only
  `Write` is added, no implicit inherit-all.
- Unsafe role name (e.g., `../foo`) → `_sanitize_role` raises `ValueError`
  inside `memory_dir`, propagates to `build_memory_section` caller in
  sdk_teammate, which logs a warning and skips injection (same behavior as
  user scope today).
- Memory index file does not yet exist for project/local scope → reuses
  existing `_read_index` "no prior memories yet" note (unchanged).
- Project root is a relative path → resolve via `Path(self._cwd).resolve()`
  before constructing memory paths so the rendered guidance contains an
  absolute path.
- Operator sets `memory: project` on a role that the running process
  happens to invoke from a different repo checkout → project root tracks
  CWD, so the memory directory is checkout-specific. This is the intended
  semantics for project scope (memory rides with the checkout).
- Operator sets `memory: local` but does not gitignore
  `.claude/agent-memory.local/` → memory will be committed if the operator
  runs `git add`. Out of scope to prevent; guidance text calls this out.

## Acceptance Tests

1. Given a teammate role declared with `memory: user`, when
   `memory_dir(role, scope="user")` is called, then it returns
   `Path.home() / ".claude" / "agent-memory" / <role>` (unchanged from
   today's one-arg form).
2. Given a teammate role declared with `memory: project` and a project
   root `R`, when `memory_dir(role, scope="project", project_root=R)` is
   called, then it returns `R / ".claude" / "agent-memory" / <role>`.
3. Given a teammate role declared with `memory: local` and a project
   root `R`, when `memory_dir(role, scope="local", project_root=R)` is
   called, then it returns `R / ".claude" / "agent-memory.local" / <role>`.
4. Given `scope` is `"project"` or `"local"` and `project_root` is `None`,
   when `memory_dir` is called, then it raises `ValueError` naming the
   missing `project_root` argument.
5. Given `build_memory_section` is called with `scope="user"`, then the
   rendered text contains the cross-project guidance phrase ("apply
   across projects") and does NOT contain project-specific or
   local-specific phrasing.
6. Given `build_memory_section` is called with `scope="project"`, then
   the rendered text emphasizes project-specific committed/shared memory,
   warns against secrets / machine-specific detail, and names the
   project-scoped path.
7. Given `build_memory_section` is called with `scope="local"`, then the
   rendered text describes machine-local non-shared memory, suitable for
   experimental notes, and recommends a `.gitignore` entry
   (`.claude/agent-memory.local/`).
8. Given an `AgentDefinition` with `memory="project"` and `tools=()`, when
   passed through `ensure_write_tool`, then the returned dataclass has
   `tools=("Write",)` and the input object is unchanged (identity).
9. Given an `AgentDefinition` with `tools=("Write", "Read")`, when passed
   through `ensure_write_tool`, then the returned object IS the input
   (no replace performed).
10. Given an `SdkTeammate` is constructed with a role whose pack declares
    `memory="project"` and `tools=()`, and with `cwd=<tmp_root>`, then
    `self._system_prompt` contains the project-scope guidance text AND
    the project-scoped directory path `<tmp_root>/.claude/agent-memory/<role>/`,
    AND `self._agents[role].tools` contains `"Write"`.
11. Given an `SdkTeammate` is constructed with `memory="local"` and
    `cwd=<tmp_root>`, then `self._system_prompt` contains the local-scope
    guidance and `<tmp_root>/.claude/agent-memory.local/<role>/`.
12. Given any of the new memory paths (project or local), when checked
    against `is_lead_project_memory_path`, then the function returns
    `False` (no false-positive collision with the lead write guard).

## Test Command

All test imports (`pytest`, `pathlib`, `claude_crew.*`) are in the
project's `pyproject.toml`. No system-level setup; the existing
`uv sync` is sufficient.

```bash
uv run pytest tests/test_teammate_memory.py tests/test_sdk_teammate.py -k "memory or scope or write_tool" -x
```

## Out of Scope

- Changing the existing `user`-scope path (`~/.claude/agent-memory/<role>/`).
- Auto-editing `.gitignore` for local scope (guidance only).
- Adding a new MCP tool surface or operator-facing CLI for memory scope.
- Changes to the lead write-guard's path-shape logic
  (`is_lead_project_memory_path`) — only verify non-collision.
- Broker, transcript, or message-bus semantics.
- Migration tooling for any existing memory directories.
- Per-scope read-time visibility (cross-scope index aggregation, etc.) —
  each scope is independent and self-contained.

## Assumptions

- **Project root for `project` / `local` scope = the teammate's
  `self._cwd`** — *Default:* fall back to `Path.cwd()` when `cwd` is None.
  *Rationale:* `self._cwd` is the standard spawn-time root for SDK
  teammates and is already used for redaction context; the fallback
  keeps legacy spawn paths working.
- **`Write` injection happens via `dataclasses.replace`, not in-place
  mutation of `role_def.tools`** — *Default:* the patched
  `AgentDefinition` replaces the entry in a fresh `self._agents` dict;
  the shared default pack stays untouched. *Rationale:* matches the
  F3b invariant verified by the existing test suite.
- **`.gitignore` entry recommendation = `.claude/agent-memory.local/`**
  — *Default:* this single line; covers the role directory tree without
  capturing `agent-memory/` (project scope, which should be committed).
  *Rationale:* mirrors the `.local`-suffix convention already used
  elsewhere in the repo.
- **Per-scope guidance constants are three full named blocks (header /
  what-to-save / what-not-to-save)** — *Default:* three sibling
  constants, picked by `scope`. *Rationale:* easier to audit and review
  than conditionals inside one shared template.
- **The existing `boundary` text mentioning `CLAUDE_CODE_DISABLE_AUTO_MEMORY`
  and the write guard stays in all three scopes** — *Default:* keep it.
  *Rationale:* the guard applies regardless of scope; the boundary
  language is correct for all three.

## Open Questions

(none)

## Validation

```bash
uv run pytest tests/test_teammate_memory.py tests/test_sdk_teammate.py
```

The validation command exercises the full memory test suite (both pure
helpers and the integration through `SdkTeammate.__init__`) so the
delivered behavior — project and local scopes resolve to real directories,
carry scope-correct guidance, and auto-attach `Write` — is observable end
to end without manual setup.

## Task Breakout

```yaml
tasks:
  - name: path-resolution-multi-scope
    description: |
      Extend memory_dir() and memory_index_path() in claude_crew/teammate_memory.py
      to accept a scope keyword (default "user") and optional project_root.
      Implement the three branch arms (user / project / local) and the
      ValueError when project/local is requested without a root. Preserve
      the existing one-positional-arg behavior so current callers and tests
      pass unchanged.
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4]
    taskTouches:
      - "claude_crew/teammate_memory.py"
      - "tests/test_teammate_memory.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_teammate_memory.py -k "memory_dir or scope" -x

  - name: per-scope-guidance-text
    description: |
      Split the module-level _INSTRUCTIONS_HEADER / _WHAT_TO_SAVE /
      _WHAT_NOT_TO_SAVE constants into three scope variants
      (user / project / local). Update build_memory_section() to accept
      a `scope` kwarg and pick the correct block. Local-scope guidance
      must recommend the `.gitignore` entry `.claude/agent-memory.local/`
      in prose (no .gitignore mutation).
    dependsOn: [path-resolution-multi-scope]
    acceptanceTests: [5, 6, 7]
    taskTouches:
      - "claude_crew/teammate_memory.py"
      - "tests/test_teammate_memory.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_teammate_memory.py -k "guidance or scope" -x

  - name: ensure-write-tool-helper
    description: |
      Add ensure_write_tool(agent_def) to teammate_memory.py. Returns the
      input unchanged if "Write" is already in tools; else returns
      dataclasses.replace(agent_def, tools=tuple(tools)+("Write",)).
      Never mutates the input. Handles tools=None and tools=() per the
      Edge Cases section.
    dependsOn: []
    acceptanceTests: [8, 9]
    taskTouches:
      - "claude_crew/teammate_memory.py"
      - "tests/test_teammate_memory.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_teammate_memory.py -k "write_tool" -x

  - name: sdk-teammate-multi-scope-injection
    description: |
      Replace the WARN-only branch in sdk_teammate.py (currently lines
      ~500-506) with full injection for all three scopes. Resolve
      project_root from self._cwd with a Path.cwd() fallback. Call
      build_memory_section with the scope and root; on ValueError from
      an unsafe role, log and skip. Then call ensure_write_tool() and,
      if it returned a replaced def, assign self._agents = {**self._agents,
      role: patched_def} so the shared pack stays untouched.
    dependsOn:
      - path-resolution-multi-scope
      - per-scope-guidance-text
      - ensure-write-tool-helper
    acceptanceTests: [10, 11]
    taskTouches:
      - "claude_crew/sdk_teammate.py"
      - "tests/test_sdk_teammate.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_sdk_teammate.py -k "memory" -x

  - name: write-guard-noncollision-regression
    description: |
      Add a regression test asserting that representative project- and
      local-scope memory paths are NOT flagged by
      is_lead_project_memory_path. No production code change — this task
      pins the documented non-collision invariant so future moves to the
      write guard cannot break it silently.
    dependsOn: [path-resolution-multi-scope]
    acceptanceTests: [12]
    taskTouches:
      - "tests/test_teammate_memory.py"
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_teammate_memory.py -k "lead_project_memory or noncollision" -x
```

## Design Notes

The user-scope `memory_dir("role")` one-positional-arg call form is
preserved by keyword defaults; existing tests in `test_teammate_memory.py`
that call `memory_dir("sentinel")` will continue to pass unchanged. This
is the load-bearing back-compat constraint — any signature shift that
breaks it would cascade through `write_guard_deny_message` and several
sdk_teammate tests.

The Write auto-attach lives in `sdk_teammate.__init__` (not
`factories.default_factory`) because the trigger is the role's `memory:`
declaration, which is read at the teammate-construction layer alongside
the rest of the memory plumbing. Putting it in factories would mean
re-reading the pack's `memory:` field a second time at a different
layer — duplicate logic and an extra coupling for no benefit.

## What Shipped

<!-- One bullet per task name from the spec's ## Task Breakout, in
     declaration order. Extracted via bin/spec-tasks.sh or equivalent.
     Populated by WHAT_SHIPPED. -->

- path-resolution-multi-scope
- per-scope-guidance-text
- ensure-write-tool-helper
- sdk-teammate-multi-scope-injection
- write-guard-noncollision-regression

## Feature Review Summary

<!-- Feature-review verdict (PASS / REQUEST-CHANGES) followed by one
     line per Critical or High finding from the feature-review report.
     Low/Advisory findings are omitted. Populated by FEATURE_REVIEW_SUMMARY.
     Format:
       Verdict: PASS
       - [High] finding title (rr-feature-reviewer, cycle N) -->

Verdict: UNKNOWN

## Retro Findings

<!-- What Went Well + What Didn't bullets from the feature-retro report
     and (when workflowRetroEnabled=true) the workflow-retro report.
     Populated by RETRO_FINDINGS.

     Skip semantics: when state.retroSkipped=true or RR_SKIP_RETRO=1,
     the extractor sets RETRO_FINDINGS to the literal text:
       _retrospective: skipped_
     No bullets are added; the section renders exactly that one line. -->

### What Went Well

- **Clean single-cycle execution across all five tasks.** Every slice built and reviewed in cycle 0 with no REQUEST-CHANGES flips. `multi-scope-agent-memory-task-path-resolution-multi-scope-slice-review-0.md` through `multi-scope-agent-memory-task-write-guard-noncollision-regression-slice-review-0.md` each returned PASS.
- **Spec architecture boundaries held in the implementation.** The decision to keep `teammate_memory.py` pure (no SDK coupling) and put injection at `sdk_teammate.__init__` paid off — all three helpers (`memory_dir`, `build_memory_section`, `ensure_write_tool`) were testable in isolation before the integration slice landed, and the feature-reviewer confirmed no skew between path shapes used at injection and those pinned by the regression test (`multi-scope-agent-memory-feature-review-0.md`, Check 1).
- **Back-compat preserved without extra scaffolding.** The keyword-default `scope="user"` design meant existing one-arg callers and all prior `test_teammate_memory.py` tests passed unchanged. `multi-scope-agent-memory-task-path-resolution-multi-scope-slice-review-0.md` confirms `test_memory_dir_user_scope_default` and `write_guard_deny_message` callers were untouched.
- **F3b shared-pack invariant correctly preserved.** `sdk-teammate-multi-scope-injection` used `self._agents = {**self._agents, role: patched_def}` fresh-dict replacement and `role_def = patched_def` local rebind so `getattr(role_def, "tools", None)` sees Write. Both the slice reviewer and feature reviewer confirmed the invariant holds (`multi-scope-agent-memory-task-sdk-teammate-multi-scope-injection-slice-review-0.md`, `multi-scope-agent-memory-feature-review-0.md`).
- **All 12 acceptance tests delivered and 184 tests pass in validation.** `multi-scope-agent-memory-validation.md` records 184/184 in 3.61s exit=0. Feature reviewer confirmed every AT covered by a directly-named test (`multi-scope-agent-memory-feature-review-0.md`, Check 2).
- **Plan review Medium findings did not block.** MEDIUM-01 (parallel `path-resolution` + `ensure-write-tool` both touching `teammate_memory.py`) was flagged in `multi-scope-agent-memory-plan-review-0.md`; implementors serialized correctly in practice. MEDIUM-02 (`tools=None` missing explicit AT) was handled in the implementation with an explicit test despite no formal AT (`multi-scope-agent-memory-task-ensure-write-tool-helper-slice-review-0.md`).

### What Didn't

- **`Scope = Literal[...]` alias declared in spec but absent from production code.** The spec's Data/API Contracts section names `Scope = Literal["user", "project", "local"]` as a module-level type alias, but the implementation uses `scope: str` on all three signatures. Flagged Info in `multi-scope-agent-memory-task-path-resolution-multi-scope-slice-review-0.md` and elevated to a four-bullet Info thread in `multi-scope-agent-memory-feature-review-0.md` (finding `feature.spec.type-drift.scope-literal`). The `else: raise ValueError` in `memory_dir` provides a runtime safety net, but the typed API surface the spec promised was not delivered.
- **Inline import in production code is unjustified.** `sdk_teammate.py:507` contains `from claude_crew.teammate_memory import build_memory_section, ensure_write_tool` inside `__init__` with no comment explaining the circular-import rationale. Flagged Info in `multi-scope-agent-memory-task-sdk-teammate-multi-scope-injection-slice-review-0.md` and confirmed in `multi-scope-agent-memory-feature-review-0.md` (finding `feature.cracks.inline-imports`). CLAUDE.md flags inline test imports as a code smell; production methods compound that.
- **`build_memory_section` else-fallthrough inconsistent with `memory_dir` raise.** `memory_dir` raises `ValueError` on an unknown scope; `build_memory_section` silently defaults to user-scope guidance. The inconsistency is currently dead (callers go through `memory_dir` first), but a future direct caller would get surprising behavior. Called out as `feature.cracks.scope-fallthrough` in `multi-scope-agent-memory-feature-review-0.md`.
- **`cwd=None` fallback not covered at the `SdkTeammate.__init__` integration layer.** AT-10 and AT-11 both pass `cwd=str(tmp_path)`. The `Path.cwd()` fallback is exercised only at the helper layer, not through the integration. Flagged `feature.test.coverage-gap.cwd-none-integration` in `multi-scope-agent-memory-feature-review-0.md`. The spec names this edge case and documents DEBUG-log behavior; a corresponding integration test is missing.

## BACKLOG Deltas

<!-- Bullet list of items added to doc/BACKLOG.md during this slice,
     sourced from the doc-sync checklist report (slug-retro-doc-sync.md).
     Populated by BACKLOG_DELTAS.
     Format: one `- item text` line per delta.

     Skip semantics: when state.retroSkipped=true or RR_SKIP_RETRO=1,
     the extractor sets BACKLOG_DELTAS to the literal text:
       _None — retro skipped._
     The section renders exactly that one line. -->

_None._

## Validation

<!-- Validation verdict (PASS / MANUAL / surgical-fix) and a one-line
     outcome statement drawn from the validation report.
     Populated by VALIDATION_SUMMARY.
     Format: `PASS — <one-line outcome>` -->

PASS

## Commits

<!-- Output of: git log --oneline main..HEAD from the slice worktree.
     Injected verbatim by the extractor. Do NOT replace with prose.
     Populated by COMMITS.

     Edge case: if the branch has no commits ahead of main (should not
     happen for a PASS slice), the extractor sets COMMITS to the literal
     text:
       _No commits on slice branch ahead of main._ -->

_No commits on slice branch ahead of main._
