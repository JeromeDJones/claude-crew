# Feature: Agent-Definition Loader (Pipeline #3b)

**Status**: Planning (Phase 1)
**Created**: 2026-04-26
**Branch**: `feature/agent-definition-loader`

---

## Phase 1: Research & Requirements

### Problem Statement

Feature #3a shipped a built-in default pack (`explorer`, `planner`, `general-purpose`) and reserved an `agents` kwarg seam on `SdkTeammate` for Feature #3b. Users — including Jerome — already have agent definitions in `~/.claude/agents/` (Jerome's: `builder`, `feature-planner`, `runner`, `scout`, `sentinel`) and may have project-level `.claude/agents/`. Today, claude-crew teammates can only delegate to the three bundled agents; the user's real agent set is invisible to them.

This blocks Feature #5 (real-task validation). Dogfooding claude-crew on a non-trivial task with only the toy three-pack tests "claude-crew works in lab conditions," not "claude-crew works as a substrate for my actual workflow." The proof point is artificially weak without #3b.

The fix is small and self-contained: walk two well-known directories, parse each `.md` with the existing `parse_pack_file`, merge with the existing `merge_packs`, hand the result to `SdkTeammate` via the existing `agents` kwarg. The new design surface is which directories, in what precedence order, and how to behave when a file is malformed or has frontmatter the SDK doesn't support.

### Success Criteria

- [ ] **SC-1 — User-level discovery.** Loader discovers every `*.md` (non-recursive, lowercase extension only) at `~/.claude/agents/` and parses each into `(key, AgentDefinition)` via `parse_pack_file`. `README.md` is excluded by name (the bundled pack ships one and the convention will spread). Glob results are sorted alphabetically before parsing so intra-directory ordering is deterministic across filesystems.
- [ ] **SC-2 — Project-level discovery.** Loader discovers every `*.md` (non-recursive, lowercase extension only, `README.md` excluded, sorted alphabetically) at `<project-root>/.claude/agents/` and parses each into `(key, AgentDefinition)` via `parse_pack_file`. "Project root" is resolved from the MCP server's CWD at startup (single value, not per-spawn).
- [ ] **SC-3 — Precedence.** Effective pack at spawn time is `merge_packs(merge_packs(default, user), project)` — project agents shadow user, user shadows default. Whole-`AgentDefinition` replacement on key collision (matching `merge_packs` semantics from #3a).
- [ ] **SC-4 — Missing directories are silent.** A user with no `~/.claude/agents/` (or no project `.claude/agents/`) sees no warning, no error, no log noise. The default pack continues to load.
- [ ] **SC-5 — Malformed files are isolated.** A single bad file (invalid YAML, missing required field, parser exception) emits a warning naming the file and the cause, is skipped, **and** valid sibling files in the same directory are still parsed and present in the returned merged pack. Test must plant both a bad and a good file in the same directory and assert (a) the bad one is absent, (b) the good one is present in the result.
- [ ] **SC-6 — Unsupported frontmatter fields warn, agent still loads.** When a file's frontmatter contains keys outside `AgentDefinition`'s accepted set (e.g., a hypothetical future `setting_sources` or a typo'd `descrption`), the loader emits a warning naming the file and the dropped key(s), and the agent loads with the supported fields only.
  - **Implementation note**: this requires new behavior. `_loader.py::_validate_frontmatter` today silently ignores unknown fields for forward-compat. SC-6 is satisfied either by adding a strict mode to `_validate_frontmatter` or by a thin wrapper around `parse_pack_file` that diffs the frontmatter dict against the accepted field set before parsing. Choice deferred to Phase 2 (Q4).
- [ ] **SC-7 — Wired through the existing seam.** The merged pack reaches `SdkTeammate` via the `agents` kwarg established in #3a. No new constructor parameters on `SdkTeammate` or `sdk_factory`. No call sites outside the spawn path see the loader.
- [ ] **SC-8 — Live E2E proof.** An integration test plants two agent files (one user-dir, one project-dir), spawns a real `SdkTeammate`, has it delegate to each, and verifies a tool-side-effect (file on disk) — not the agent's narration. Live-probe checklist applies (TEMPLATE.md Phase 3 §"Live-probe checklist").
- [ ] **SC-9 — Configurable directory roots for tests.** The loader's directory roots are injectable (parameterizable) so the live E2E in SC-8 can plant fixtures in a tempdir without touching the real `~/.claude/agents/`. Default values resolve to the production paths.

### Questions

- [x] **Q1 — Precedence: project shadows user, or user shadows project?** *Answer: project shadows user.* Matches Claude Code's own rule (project-level `.claude/` overrides user-level `~/.claude/` for memory and settings) and matches developer intuition (the repo I'm in says what runs here).
- [x] **Q2 — Recursive glob or flat?** *Answer: flat (`*.md` at the directory root only, no subdirs).* Claude Code's loader is flat; matching it avoids surprise. If users want grouping later, that's a follow-up.
- [x] **Q3 — Project root: where does it come from?** *Answer: the MCP server's CWD at startup, resolved once and reused for the lifetime of the process.* The MCP server is invoked per-project. Resolving per-spawn would let teammates pick up a different project mid-session, which is footgun-shaped.
- [x] **Q4 — Should the bundled `parse_pack_file` change behavior, or do we need a thin wrapper for the user/project path?** *Defer to Phase 2.* This is a design call (one parser with a strict-mode flag, vs. two parsers with shared internals). Both are cheap. Resolved during Phase 2 design.
- [ ] **Q5 (Phase 2) — Warning mechanism.** `logging.warning` (named logger, configurable level) vs. `warnings.warn` (Python warnings system, filterable per-category). SC-5 and SC-6 tests need to assert on something concrete. Phase 2 to pin: which mechanism, what logger name / category, what message format. Recommendation entering Phase 2: `logging.warning` on a `claude_crew.subagents.loader` logger, since stdlib `warnings` filters are per-process and surprise.
- [ ] **Q6 (Phase 2) — Resource limits.** What's the loader's behavior for pathological inputs — 500 files in one directory, or a single 50MB `.md`? At MCP startup, blocking on `read_text()` of a huge file blocks the server. Options: (a) cap per-file size before read (e.g., 256KB; reject larger with a warning), (b) cap directory file count (e.g., 100; warn-and-stop beyond), (c) accept the risk because this is a local user tool, not a service. Phase 2 to decide.
- [ ] **Q7 (Phase 2) — Shadowing observability.** When a project agent shadows a user agent with the same key, is that silent (per `merge_packs`'s current contract) or does it emit an info/warning? Silent shadowing is surprising for a user debugging "why doesn't my user-level `planner` work here?" Phase 2 to decide.
- [ ] **Q8 (Phase 2) — Intra-directory key collision.** Two files in the same directory that produce the same kebab-key (e.g., a hand-authored `general-purpose.md` colliding with the bundled `general_purpose.md` if a user copies one out of `claude_crew/subagents/`). Alphabetical sort + last-wins is documented in SC-1/SC-2, but Phase 2 should decide whether to also emit a warning on intra-dir collision (it's almost always a user mistake, not intent).

### Constraints & Dependencies

- **Requires**:
  - `claude_crew/subagents/_loader.py::parse_pack_file` (#3a, in tree)
  - `claude_crew/subagents/__init__.py::merge_packs` (#3a, in tree)
  - `SdkTeammate.__init__` `agents` kwarg (#3a, in tree)
- **Breaking changes**: None to public API. New observable behavior: warnings on unsupported frontmatter fields for user/project files.
- **Performance**: Negligible. Two directory walks at spawn time, max ~tens of small `.md` files. No recursion. No long-running I/O.
- **Security**: User-supplied agent files run with the same trust as the parent process. The user wrote them in their own `~/.claude/`. No sandboxing introduced or required.
- **CLAUDE.md inheritance**: User-loaded subagents inherit the parent's CLAUDE.md the same way bundled-pack subagents do (per `doc/research/sdk-subagents.md`). #3b does not change this; isolation remains a teammate-level concern.

### Out of Scope (logged for later)

- Per-teammate selection ("teammate X gets agents A, B; teammate Y gets B, C only") — deferred per #3a OOS.
- Field-level merge — `merge_packs` is whole-entry replacement and stays that way.
- Hot reload — agents discovered once at MCP-server startup. Restart to pick up changes.
- Recursive directory globs / namespaces.
- Validation that user-supplied `model` strings are valid SDK model ids — pass through; the SDK rejects bad ids at spawn.

**Gate**: Questions answered, success criteria measurable, constraints documented, Sentinel review of Phase 1 complete, co-architect review of Phase 1 complete, user confirmed.

---

## Phase 2: Design Pin-Downs (plan-mode replaces full Phase 2)

Per gate decision (2026-04-26): Phase 1 is sharp enough that full Phase 2 ceremony doesn't pay back. Building from Phase 1 with the following design decisions pinned. Each decision names where it's enforced.

- **Q4 → Wrapper, not strict-mode flag.** New `_user_loader.py::strict_parse(path)` wraps `parse_pack_file(path)`: it diffs the frontmatter dict against the accepted `AgentDefinition` field set *before* parsing and warns on extras. `parse_pack_file` itself stays untouched (bundled pack remains forward-compat-silent). *Carried into:* `_user_loader.strict_parse`, `tests/subagents/test_user_loader.py::test_unsupported_frontmatter_warns_and_loads`.
- **Q5 → `logging.warning` on `claude_crew.subagents.loader`.** Tests assert via `caplog`. `warnings.warn` rejected: process-wide filters surprise users, and we want every warning visible in the MCP server log without users learning Python's warnings system. *Carried into:* `_user_loader.logger`, every warning-asserting test using `caplog.set_level(logging.WARNING, logger="claude_crew.subagents.loader")`.
- **Q6 → 256 KB per-file cap, 100 files per directory cap.** Constants `_MAX_FILE_BYTES = 256 * 1024` and `_MAX_FILES_PER_DIR = 100` at module top. Files larger than the cap: warn + skip. Directories with more than the cap: warn + take the first 100 sorted, skip the rest. *Carried into:* `_user_loader.discover_dir`, `tests/subagents/test_user_loader.py::test_file_size_cap`, `test_file_count_cap`.
- **Q7 → Info log on shadow, not warning.** When project agent shadows user (or user shadows default), emit `logger.info(f"agent {key!r} from {project_path} shadows {user_path}")`. Info, not warning, because shadowing is the documented contract — not a problem, but useful when debugging "why does my agent behave differently here." *Carried into:* `_user_loader.build_merged_pack`, `tests/test_user_loader.py::TestShadowingObservability` (covers user-shadows-default, project-shadows-user, and project-shadows-default-with-no-user).
- **Q8 → Warn on intra-dir kebab-key collision, last-wins alphabetical.** Two files in the same directory producing the same kebab-key: emit warning naming both files and which one wins; alphabetically-later file wins (matches the deterministic glob order from SC-1). *Carried into:* `_user_loader.discover_dir`, `tests/subagents/test_user_loader.py::test_intra_dir_collision_warns`.

---

## Phase 3: Task Breakdown
*(not yet started)*

---

## Phase 4: Implementation
*(not yet started)*

---

## Phase 5: Completion
*(not yet started)*
