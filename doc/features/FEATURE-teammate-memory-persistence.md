# Feature: Teammate Memory Persistence

**Status**: Planning
**Created**: 2026-05-04

---

## Phase 1: Research & Requirements

### Problem Statement

Top-level teammates (spawned via `spawn_teammate`) have no persistent memory across claude-crew sessions. Within a session, conversation history is automatic via the SDK's subprocess/session_id mechanism. But when a session ends and a new one begins, a teammate spawned with the same role starts completely fresh — no accumulated observations, no learned preferences, no prior context.

The `memory` field already exists in `PackFrontmatter` and passes through to `AgentDefinition`, but it's a no-op for top-level teammate spawning (the SDK's `ClaudeAgentOptions` has no `memory` carrier). The spawn path emits a WARN to alert operators that the field does nothing. Claude Code's auto-memory subsystem (`~/.claude/projects/<encoded-cwd>/memory/`) is NOT activated by the SDK-spawned CLI subprocess — confirmed by `scripts/auto_memory_probe.py` and documented in `doc/research/sdk-memory.md §3`.

**The gap is solvable without SDK changes.** The probe confirmed that teammates CAN read and write the conventional memory path — the SDK just doesn't activate it automatically. The right fix: at spawn time, read the teammate's role-scoped memory file (if it exists) and inject it into the system prompt, then instruct the teammate to write updates back to that path using the Write tool. Same primitive Claude Code uses; explicitly wired rather than automatic.

The `memory: user` frontmatter declaration becomes the opt-in signal for this behavior, turning a currently-broken field into a working one.

### Success Criteria

- [x] **SC-1: Memory file content injected at spawn.** When a pack declares `memory: user` and a role-scoped memory file exists on disk, the teammate's system prompt at spawn includes the file's content as a clearly-delimited "Memory from prior sessions" section.
- [x] **SC-2: Memory instructions injected when no file exists.** When a pack declares `memory: user` and no memory file exists yet, the teammate's system prompt includes a section explaining the role-scoped memory path and instructing the teammate to create and maintain the file.
- [x] **SC-3a: Teammate with Write tool can persist memory.** A teammate with `memory: user` and the Write tool available can write content to its role-scoped path; the file exists on disk after the write and its content matches what was written.
- [x] **SC-3b: Teammate without Write tool receives an honest acknowledgement.** When a pack declares `memory: user` but the teammate's tool list excludes Write, the injected memory section explicitly states that memory updates cannot be persisted (the teammate lacks the Write tool) — no silent no-op.
- [x] **SC-4: Memory is role-scoped and distinct within a project.** A `sentinel` teammate and a `builder` teammate have distinct memory file paths within the same project; writing to one cannot affect the other. Two different role names within the same project always produce two different paths. *(Note: the cwd encoding is not injective over all possible filesystem paths — projects whose paths differ only by hyphens vs. slashes would share a namespace. This is inherited from the CLI's own convention, not introduced here.)*
- [x] **SC-5: Memory persists across sessions (live test).** Content written by a teammate in session N appears in the injected memory section when the same role is spawned in session N+1. *Verification requires a live SDK test (`CLAUDE_CREW_LIVE_TESTS=1`); stub-mode tests cannot satisfy this criterion alone.*
- [x] **SC-6: No memory injection when `memory` not declared.** Packs without `memory: user` in frontmatter receive no memory section in their system prompt — no behavior change for existing packs.
- [x] **SC-7: No WARNING-level log emitted at spawn for `memory: user`.** A teammate spawned from a pack that declares `memory: user` produces no `WARNING`-level log entry related to the memory field. A `DEBUG`-level confirmation of injection is acceptable.

### Questions

- [x] **Does the memory path need to be writable by SDK-spawned teammates?** Yes — confirmed by `auto_memory_probe.py` (path access) and `scripts/memory_write_spike.py` (live Write tool call, 2026-05-04). An SDK-spawned teammate with `tools=["Write"]` can write to `memory/<role>.md` without permission blocks or hook interference. File created, content verified, test passed.
- [x] **Should we share the path with Kael's `MEMORY.md`?** No — per-role namespace under `teammates/` avoids collisions. Kael writes `memory/MEMORY.md`; a sentinel teammate writes `memory/teammates/sentinel.md`.
- [x] **Can the injection happen in `build_teammate_prompt`?** Yes — it's the right layer. `build_teammate_prompt` already assembles the full system prompt from pack body + addendum; the memory section is another addendum section. The encoded-cwd is derivable from `os.getcwd()` at spawn time using the same convention Claude Code uses.
*(No open questions — all resolved during Phase 1.)*

### Constraints & Dependencies

- Pure internal change — no MCP tool surface changes, no API changes, no dashboard changes.
- Injection happens in `claude_crew/teammate_prompt.py` (`build_teammate_prompt`) or its call site in `sdk_teammate.py`.

**Memory path convention (empirically validated 2026-05-04, corrected after CLI subagent probe):**
- Role memory directory: `~/.claude/agent-memory/<role>/` (user-scoped, NOT project-scoped)
- Per-role index: `~/.claude/agent-memory/<role>/MEMORY.md`
- Detail files: agent picks topic-named files inside the directory
- Same path the CLI auto-loads when a subagent with `memory: user` is dispatched — parity ensures CLI subagent and SDK teammate share memory for the same role.

**Earlier (wrong) assumption:** Phase 1 originally targeted `~/.claude/projects/<encoded-cwd>/memory/<role>.md` — that's Kael's project-scoped memory, a different system. A live probe (sentinel subagent asked to "remember" something with no path hint) wrote to `~/.claude/agent-memory/sentinel/`, confirming the correct path.

**What auto-loads vs what requires injection (corrected):**
- For CLI subagents with `memory: user`: the CLI provides scaffolding (location guidance, save/skip rules, format) AND auto-loads the first 200 lines of the role's MEMORY.md
- For SDK teammates (this feature): the SDK provides nothing. We inject equivalent scaffolding + the first 200 lines of the role's MEMORY.md at spawn time.

**Design principle: SDK teammate behavior must match CLI subagent behavior.** Same role, same memory, regardless of execution context.

- **`memory: user` only for v1.** `project` and `local` are deferred. A pack declaring `memory: project` or `memory: local` continues to parse and validate but produces the existing WARN.
- **Concurrent same-role spawns are out of scope for v1.** Last-write-wins, no protection. Operators who spawn multiple same-role teammates with `memory: user` do so at their own risk.
- The `memory` field on `PackFrontmatter` and `AgentDefinition` already exists and validates — no loader changes needed.
- Must not break the 855-test suite. `tests/test_teammate_prompt.py` tests `build_teammate_prompt` directly and will need updating for the new memory section. A live SDK test is required for SC-5.

**Gate**: ✅ Questions answered, success criteria measurable and testable, constraints documented, sentinel findings addressed.

---

## Phase 2: Design & Specification

### Architecture

**Injection point:** `SdkTeammate.__init__` in `sdk_teammate.py`, in the block that assembles `self._system_prompt` (lines ~409–417). Before calling `build_teammate_prompt`, it computes a `memory_section` string and passes it through.

**Prompt assembly change:** `build_teammate_prompt` in `teammate_prompt.py` gains a `memory_section: str | None = None` parameter. When non-None, the section is appended after `SENTINEL_ANTIPATTERNS` as a fifth addendum section, separated by `"\n\n"`. The four existing sentinels and their ordering are unchanged — existing tests continue to pass without modification.

**New module:** `claude_crew/teammate_memory.py` owns all memory I/O and section-building logic. This keeps `teammate_prompt.py` a pure string-assembly module and isolates file I/O for targeted testing.

**Warning suppression:** The existing `logger.warning(...)` in `_run()` for the `memory` field fires for any non-None memory value on every turn. This is replaced by targeted handling in `__init__`:
- `memory == "user"` → inject section, emit `logger.debug(...)` (no warning)
- `memory in ("project", "local")` → emit `logger.warning(...)` once in `__init__`, not per-turn

### New Module: `claude_crew/teammate_memory.py`

`SENTINEL_MEMORY` lives in `teammate_prompt.py` alongside the other `SENTINEL_*` constants — they are the public test surface for prompt ordering assertions. `teammate_memory.py` imports it from there.

```
_MAX_MEMORY_BYTES: int = 51_200                  # 50 KB cap; truncate with note if over

def _encode_cwd() -> str
    # "-" + os.getcwd().strip("/").replace("/", "-")

def _sanitize_role(role: str) -> str
    # Allow [a-zA-Z0-9_-] only; raise ValueError on unsafe input
    # Prevents path traversal: role "../../etc" → ValueError

def memory_file_path(role: str) -> Path
    # ~/.claude/projects/<encoded-cwd>/memory/<sanitized_role>.md
    # Pure — no I/O

def memory_index_path() -> Path
    # ~/.claude/projects/<encoded-cwd>/memory/MEMORY.md
    # Pure — no I/O

def build_memory_section(role: str, tools: tuple[str, ...] | None) -> str
    # Reads memory_file_path(role) if it exists
    # Computes has_write = "Write" in (tools or ())
    # Returns formatted section string (see Content Contracts below)
    # Never raises — I/O errors are caught and reflected as "unavailable" note
```

### `build_teammate_prompt` signature change

```python
def build_teammate_prompt(
    role: str,
    pack_body: str,
    agents: dict[str, Any],
    memory_section: str | None = None,   # NEW
) -> str:
```

When `memory_section` is not None, the assembled prompt is:

```
<pack_body>

## Operating context
...
## Available teammates
...
## Delegation
...
## Anti-patterns
...

<memory_section>
```

The memory section is separated from anti-patterns by `"\n\n"` — same separator used between all other addendum sections.

### `SdkTeammate.__init__` change (spawn-time wiring)

```python
# Existing block (lines ~409-417), extended:
role_def = self._agents.get(role)
role_memory = getattr(role_def, "memory", None)

# Warn once for unsupported values; suppress entirely for "user" (injection handles it).
if role_memory in ("project", "local"):
    logger.warning(
        "teammate=%s role=%s pack declares memory=%r; only 'user' is "
        "supported in v1 — no injection performed",
        self.id, role, role_memory,
    )

if system_prompt is not None:
    self._system_prompt = system_prompt
else:
    _body = self._pack_bodies.get(role)
    if _body is not None:
        # Memory section computed here, inside the else, so we skip I/O
        # entirely when an explicit system_prompt override is active.
        memory_section = None
        if role_memory == "user":
            try:
                from claude_crew.teammate_memory import build_memory_section
                memory_section = build_memory_section(
                    role, getattr(role_def, "tools", None)
                )
                logger.debug(
                    "teammate=%s role=%s memory section injected", self.id, role
                )
            except ValueError:
                logger.warning(
                    "teammate=%s role=%s memory injection skipped: "
                    "role name contains unsafe characters",
                    self.id, role,
                )
        self._system_prompt = build_teammate_prompt(
            role, _body, self._agents, memory_section=memory_section
        )
    else:
        self._system_prompt = _default_system_prompt(role)
```

The existing `memory` warning block in `_run()` is removed entirely (its logic moves to `__init__` above).

**Key design note (from Sentinel H-2):** memory_section computation is inside the `else` branch — it only runs when no `system_prompt` override is active. This avoids unnecessary file I/O and prevents confusing warnings for spawns where memory injection would be silently discarded anyway.

### Content Contracts

#### SC-1: Memory file exists

```
## Memory from prior sessions

The following is your accumulated memory from prior sessions. Read it before
beginning work — it contains observations, preferences, and context that
should inform your responses.

---
<file content verbatim, truncated at 50 KB with note if over limit>
---

**Memory file path:** `~/.claude/projects/<encoded-cwd>/memory/<role>.md`

To update your memory: overwrite this file using the Write tool. Follow the
same frontmatter format shown in the file.

To keep the MEMORY.md index current: if `~/.claude/projects/<encoded-cwd>/memory/MEMORY.md`
does not already have an entry for `<role>.md`, append:
`- [<Role> memory](<role>.md) — <one-line description of this role's memories>`
```

#### SC-2: No memory file yet

```
## Memory from prior sessions

You have no stored memory for this role yet. When you accumulate observations
or preferences worth carrying into future sessions, write them to:

  `~/.claude/projects/<encoded-cwd>/memory/<role>.md`

Use the standard Claude Code memory frontmatter:

  ---
  name: <Role> memory
  description: one-line summary of what this file contains
  type: user
  ---

  Your memory content here.

After creating the file, add an index entry to MEMORY.md:
  `~/.claude/projects/<encoded-cwd>/memory/MEMORY.md`
Append: `- [<Role> memory](<role>.md) — <one-line hook>`
```

#### SC-3b: Write tool absent (appended to either variant above)

```
**Note:** The Write tool is not in your tool list. Memory updates cannot be
persisted from this session. If you need memory persistence, ask your operator
to add `Write` to this pack's `tools:` declaration.
```

### Role Name Sanitization

`_sanitize_role` allows only `[a-zA-Z0-9_\-]`. Any role name containing path separators, dots, or other special characters raises `ValueError`. The caller in `__init__` catches `ValueError` and emits a warning with this content contract:

```
"teammate=%s role=%s memory injection skipped: role name contains unsafe characters"
```

No memory section is injected — fail safe. All known well-formed packs use kebab-case role names and pass this filter.

**Valid examples:** `sentinel`, `builder`, `general-purpose`, `rr-planner`  
**Rejected examples:** `../../etc/passwd`, `foo/bar`, `role with spaces`

In practice, pack frontmatter keys follow kebab-case convention and all known role names pass this filter.

### Memory File Size Cap

If `memory_file_path(role).stat().st_size > _MAX_MEMORY_BYTES` (50 KB), only the first 50 KB is injected, followed by:

```
[... truncated at 50 KB — full file at <path> ...]
```

50 KB is generous for structured memory files. Files that large likely contain accumulated cruft; the teammate should prune them. No hard failure — inject what we have.

### MEMORY.md Mutation Policy

The server does **not** mutate `MEMORY.md` at spawn time. Reasons:
1. MEMORY.md is a shared file; concurrent spawns could race on writes
2. Kael manages this file; server-side mutation would be unexpected
3. The teammate is better positioned to add the entry when it first writes memory

The injected instructions tell the teammate to add the index entry when creating or updating the memory file. The entry is idempotent to add (teammates can check before appending).

### has_write Canonical Check

The `has_write` flag in `build_memory_section` determines whether persistence instructions are included. The canonical expression:

```python
has_write = "Write" in (tools or ())
```

This handles all cases: `tools=None` (no tools attribute), `tools=()` (empty tuple, no tools declared), `tools=("Read", "Write", "Bash")` (Write present). Do not use `tools is not None and "Write" in tools` — that fails when tools is None.

### Edge Cases

| Scenario | Behavior |
|---|---|
| `memory: user`, no memory dir yet | `memory_file_path` returns path even if parent dirs don't exist; `build_memory_section` checks `path.exists()` → SC-2 injection |
| `memory: user`, I/O error reading file | Catch `OSError`; inject SC-2 variant with note "memory file unreadable" |
| `memory: user`, file over 50 KB | Inject first 50 KB + truncation note |
| `memory: user`, file has malformed frontmatter | Inject raw file content verbatim — no attempt to parse frontmatter |
| Role name contains path separator | `ValueError` from `_sanitize_role`; `__init__` catches, warns (see content contract above), no injection |
| `system_prompt` override is set | Computation skipped entirely — memory_section is never computed (moved inside else branch to avoid wasted I/O) |
| `memory: project` or `memory: local` | Warning in `__init__` once; no injection |
| `memory: user`, `tools` is `None` (not declared) | `has_write = False` → SC-3b note appended |
| `memory: user`, `tools` is empty tuple | `has_write = False` → SC-3b note appended |
| `memory: user`, `tools` contains `"Write"` | `has_write = True` → full persistence instructions |
| Two teammates with same role spawned concurrently | Last write wins on memory file; no protection (out of scope v1) |
| Two different-role teammates both creating first memory entries | Both may append to MEMORY.md simultaneously → duplicate entries. Accepted risk v1; MEMORY.md duplicates are cosmetic, not functional (detail file injection is unaffected). |

### Cross-Feature Impact

**Consumers of `build_teammate_prompt`:**
- `sdk_teammate.py:SdkTeammate.__init__` — the only production caller; updated above
- `tests/test_teammate_prompt.py` — tests call `build_teammate_prompt(role, body, agents)` directly; adding `memory_section=None` default means no changes needed in existing tests

**Consumers of `AgentDefinition.memory` field:**
- `sdk_teammate.py:_run()` — current warning; replaced by `__init__` handling above

**No changes to:**
- `_loader.py` — `PackFrontmatter.memory` and validation unchanged
- `factories.py` — no spawn-path changes
- `server.py` — no API surface changes
- `broker.py` — no registry changes
- `envelope.py` — no wire format changes

### New File Inventory

| File | Action |
|---|---|
| `claude_crew/teammate_memory.py` | **New** — all memory I/O and section-building |
| `claude_crew/teammate_prompt.py` | **Modify** — add `SENTINEL_MEMORY`, `memory_section` param |
| `claude_crew/sdk_teammate.py` | **Modify** — `__init__` wiring + remove `_run()` warning block |
| `tests/test_teammate_memory.py` | **New** — unit tests for memory module |
| `tests/test_teammate_prompt.py` | **Modify** — add memory section tests |
| `tests/test_live_sdk.py` | **Modify** — add SC-5 live test |

### Known Limitations (accepted, v1)

- **Server cwd vs. teammate cwd parameter:** `_encode_cwd()` uses `os.getcwd()` at construction time — the server process's cwd. If a teammate is spawned with `cwd=` set to a different directory, its injected memory path uses the server's cwd, not the teammate's. In practice all spawns share the server's project cwd. Documented; out of scope v1.
- **MEMORY.md append race:** Multiple teammates creating their first memory entries simultaneously may append duplicate lines to MEMORY.md. Duplicates are cosmetic — the detail file injection at spawn is unaffected. Accepted risk; out of scope v1.
- **cwd encoding non-injectivity:** `_encode_cwd` maps `/` to `-`, so projects whose paths differ only by hyphens vs. slashes share a namespace. Inherited from CLI convention; not introduced here.

### Assumptions

- **A-1:** Role names in the wild are valid kebab-case identifiers — the sanitization filter will not reject any legitimate pack. *(Accept unless a real pack with exotic role names surfaces.)*
- **A-2:** 50 KB is an adequate cap for memory file injection. Teammate-authored memory files rarely exceed a few KB; the cap exists as a safety bound, not a practical limit. *(Accept.)*
- **A-3:** The memory file is always a plain UTF-8 text file. No encoding detection. *(Accept — consistent with how Kael's memory files work.)*
- **A-4:** Teammates that declare `memory: user` but lack the Write tool are a valid, if limited, configuration (read-only memory access). The SC-3b note informs but does not block spawn. *(Accept.)*

### Open Questions

*(None — all resolved during Phase 1 research and Phase 2 design.)*

**Gate:** ✅ Phase 1 SCs traced to concrete design decisions. ✅ Architecture clear and implementable. ✅ Edge cases documented. ✅ Sentinel reviewed — all H findings addressed, M/L findings accepted with documentation or deferred to Phase 3 test coverage.

---

## Phase 3: Task Breakdown

### Task 1 — `teammate_memory` module + `SENTINEL_MEMORY` constant

**Scope:** New `claude_crew/teammate_memory.py` and `SENTINEL_MEMORY` added to `teammate_prompt.py`. No wiring yet — pure units.

**Files touched:** `claude_crew/teammate_prompt.py`, `claude_crew/teammate_memory.py` (new), `tests/test_teammate_memory.py` (new)

**BDD Scenarios:**

```
Scenario: memory_file_path returns correct path for a known role
  Given cwd is /home/jerome/dev/claude-crew
  When memory_file_path("sentinel") is called
  Then the result is ~/.claude/projects/-home-jerome-dev-claude-crew/memory/sentinel.md

Scenario: _sanitize_role rejects path traversal
  When _sanitize_role("../../etc/passwd") is called
  Then ValueError is raised

Scenario: _sanitize_role accepts valid kebab-case role
  When _sanitize_role("rr-planner") is called
  Then it returns "rr-planner"

Scenario: build_memory_section — no memory file
  Given the role memory file does not exist
  When build_memory_section("sentinel", ("Read", "Write")) is called
  Then the result contains SENTINEL_MEMORY
  And the result contains the memory file path
  And the result does NOT contain the SC-3b "Write tool not in your tool list" note

Scenario: build_memory_section — memory file exists
  Given a memory file exists with content "prior observations here"
  When build_memory_section("sentinel", ("Read", "Write")) is called
  Then the result contains SENTINEL_MEMORY
  And the result contains "prior observations here"
  And the result contains the memory file path

Scenario: build_memory_section — no Write tool
  Given the role memory file does not exist
  When build_memory_section("sentinel", ()) is called
  Then the result contains the SC-3b note about Write tool absent

Scenario: build_memory_section — tools is None
  When build_memory_section("sentinel", None) is called
  Then the result contains the SC-3b note (same as empty tuple)

Scenario: build_memory_section — file over 50 KB
  Given a memory file exists with 51 200 bytes of content
  When build_memory_section("sentinel", ("Write",)) is called
  Then the result contains the truncation note

Scenario: build_memory_section — I/O error reading file
  Given the memory file exists but is unreadable (permission denied)
  When build_memory_section("sentinel", ("Write",)) is called
  Then the result contains SENTINEL_MEMORY
  And the result contains a note that the file is unreadable
  And no exception is raised

Scenario: MEMORY.md not written by build_memory_section
  Given the MEMORY.md index does not exist
  When build_memory_section("sentinel", ("Write",)) is called
  Then MEMORY.md is still not created
```

**Verification command (must fail without the feature):**
```bash
uv run pytest tests/test_teammate_memory.py -v
```

**Dependencies:** None.

---

### Task 2 — Extend `build_teammate_prompt` with `memory_section`

**Scope:** Add `memory_section: str | None = None` parameter to `build_teammate_prompt`. When provided, append after `SENTINEL_ANTIPATTERNS`. All existing tests must continue to pass unchanged.

**Files touched:** `claude_crew/teammate_prompt.py`, `tests/test_teammate_prompt.py`

**BDD Scenarios:**

```
Scenario: memory_section=None — existing behavior unchanged
  Given any call to build_teammate_prompt without memory_section
  Then the result is identical to the pre-feature output
  And SENTINEL_MEMORY does not appear in the result

Scenario: memory_section provided — appended after anti-patterns
  Given memory_section="## Memory from prior sessions\n\nsome content"
  When build_teammate_prompt(role, body, agents, memory_section=memory_section)
  Then SENTINEL_MEMORY appears in the result
  And SENTINEL_MEMORY appears after SENTINEL_ANTIPATTERNS

Scenario: existing sentinel ordering tests still pass
  When build_teammate_prompt is called without memory_section
  Then SENTINEL_CONTEXT < SENTINEL_PEERS < SENTINEL_DELEGATION < SENTINEL_ANTIPATTERNS
  (all four existing ordering assertions green)
```

**Verification command (must fail without the feature):**
```bash
uv run pytest tests/test_teammate_prompt.py -v
```

**Dependencies:** None (can run in parallel with Task 1).

---

### Task 3 — Wire memory injection in `SdkTeammate.__init__`

**Scope:** Connect Tasks 1 and 2. Add memory injection block in `__init__`, remove `_run()` warning block, update warning logic. This is the only task that touches `sdk_teammate.py`.

**Files touched:** `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py`

**BDD Scenarios:**

```
Scenario: memory="user" — SENTINEL_MEMORY appears in system prompt
  Given a pack with memory="user" and tools=("Read", "Write")
  When SdkTeammate is constructed for that role
  Then teammate._system_prompt contains SENTINEL_MEMORY
  And no WARNING-level log is emitted

Scenario: memory="user" — no WARNING emitted (SC-7)
  Given a pack with memory="user"
  When SdkTeammate is constructed
  Then caplog has no WARNING entries containing "memory"

Scenario: memory="project" — WARNING emitted, no injection
  Given a pack with memory="project"
  When SdkTeammate is constructed
  Then a WARNING is emitted
  And teammate._system_prompt does NOT contain SENTINEL_MEMORY

Scenario: system_prompt override suppresses memory injection
  Given a pack with memory="user"
  And system_prompt="explicit override" is passed to the constructor
  When SdkTeammate is constructed
  Then teammate._system_prompt == "explicit override"
  And SENTINEL_MEMORY is not present
  And no file I/O occurred (memory_file_path never called)

Scenario: memory="user", role name with path separator — warning, no injection
  Given a role name containing "/"
  When SdkTeammate is constructed
  Then a WARNING is emitted containing "unsafe characters"
  And teammate._system_prompt does NOT contain SENTINEL_MEMORY

Scenario: memory not declared — no injection, no warning
  Given a pack with memory=None
  When SdkTeammate is constructed
  Then teammate._system_prompt does NOT contain SENTINEL_MEMORY
  And no WARNING emitted for memory (SC-6)
```

**Verification command (must fail without the feature):**
```bash
uv run pytest tests/test_sdk_teammate.py -v -k "memory"
```

**Dependencies:** Tasks 1 and 2 must be complete.

---

### Task 4 — E2E live test (SC-5 + full pipeline)

**Scope:** Live tests proving memory persists across sessions and that no server-side MEMORY.md mutation occurs. Runs under `CLAUDE_CREW_LIVE_TESTS=1`.

**Files touched:** `tests/test_live_sdk.py`

**BDD Scenarios:**

```
Scenario: SC-5 — content written in session N appears in session N+1
  Given a role "live-memory-probe" with memory="user" and tools=["Write"]
  And no prior memory file exists for that role

  # Session N
  When a teammate is spawned for that role
  And it is asked to write a unique marker string to its memory file
  And the broker is shut down

  Then the memory file exists on disk with the marker string

  # Session N+1
  When a new broker is created and the same role is spawned again
  And the teammate is asked "what do you remember?"
  Then the response contains the marker string
  And the memory file is cleaned up

Scenario: MEMORY.md not written by server at spawn
  Given the MEMORY.md index exists (current project index)
  When a teammate with memory="user" is spawned
  Then the MEMORY.md content is identical before and after spawn
  (server did not append an index entry)
```

**Verification command (must fail without the feature):**
```bash
CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_sdk.py -v -k "memory"
```

**Dependencies:** Task 3 must be complete.

---

### Implementation order

```
Tasks 1 + 2 (parallel) → Task 3 → Task 4
```

**Gate:** ✅ 4 tasks, each independently testable. ✅ E2E live test covers happy path (SC-5) and negative (MEMORY.md not mutated). ✅ Verification commands fail without the feature. ✅ Dependencies minimal and explicit.

---

## Phase 4: Implementation

*Pending Phase 3 gate.*

---

## Phase 5: Completion

### Verification
- [ ] Feature works against Phase 1 success criteria (SC-1 through SC-7)
- [ ] No regressions — full test suite passes (`uv run pytest`)
- [ ] Spec updated to match implementation
- [ ] PRODUCT-VISION.md updated with feature in pipeline

### Retrospective

**What went well**:

**What was friction**:

**Improvements**:

**Gate**: Feature verified, retrospective captured.
