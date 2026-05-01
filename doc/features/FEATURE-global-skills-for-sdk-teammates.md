# Feature: Global Skills Support for SDK Teammates

**Status**: In Progress (Phase 1)
**Created**: 2026-05-01
**Vision row**: #23 (M, capabilities #1, #2)

---

## Phase 1: Research & Requirements

### Problem Statement

Operators have invested in skills like `/sdd-workflow`, `/security-review`, `/docs-maintain`, `/crew-showcase` that live at `~/.claude/skills/<name>/SKILL.md` and `<project>/.claude/skills/<name>/SKILL.md`. The lead Claude Code session can invoke them. SDK teammates spawned by claude-crew today cannot — they get the agent surface (#10/#11/#21) but not the skill surface. The toolkit doesn't transfer to spawned teammates.

As role packs expand (#15 adds reviewer + runner) and crews take on real product work, having a teammate be able to run, e.g., `/sdd-workflow` itself — or a reviewer being able to run `/security-review` — closes the parity gap with the lead session and unlocks compositional workflows.

**Why now**: surfaced 2026-05-01 during `/crew-showcase` work. Natural follow-on after #11 (lightweight subagent context) and #21 (teammate prompt parity) shipped. The skill surface is the remaining identity-parity gap.

### Spike Findings (Phase 1 Research)

The Phase 1 spike unexpectedly answered the central mechanism question quickly. Key facts:

- **`claude-agent-sdk 0.1.68` natively supports skills.** Two surfaces:
  - `ClaudeAgentOptions.skills: list[str] | Literal["all"] | None` (`types.py:1528`) — top-level session filter
  - `AgentDefinition.skills: list[str] | None` (`types.py:91`) — per-role allowlist in the agents pack
- **The plumbing is already wired in claude-crew**:
  - `PackFrontmatter.skills: tuple[str, ...] | None` exists at `claude_crew/subagents/_loader.py:42` (added in #10)
  - `parse_pack_text` maps `fm.skills` → `AgentDefinition.skills` at `_loader.py:124-125`
  - `sdk_teammate.py:891-898` extracts `role_def.skills` and sets `opts_kwargs["skills"]` on `ClaudeAgentOptions`
- **The CLI translation is in the SDK** (`transport/subprocess_cli.py:165-201`, `_apply_skills_defaults`):
  - `skills="all"` → `--allowedTools Skill`
  - `skills=["foo","bar"]` → `--allowedTools Skill(foo) Skill(bar)`
  - **If `setting_sources is None`, it auto-injects `["user","project"]`** so the CLI can discover SKILL.md files. **If `setting_sources` is anything else (including `[]`), it does NOT override.**
- **Operator skill inventory** (current state, per-2026-05-01):
  - `~/.claude/skills/`: 8 skills (sdd-workflow, deep-build, docs-{seed,scaffold,maintain}, product-vision, daily-fde-digest, principal-eng-digest)
  - `<crew>/.claude/skills/`: 1 skill (crew-showcase)
  - All use 3 frontmatter fields: `name`, `description`, `tools`. None declare model/permissionMode/mcpServers.

The mechanism question is resolved: **route skills via the existing pack frontmatter + AgentDefinition.skills path**. No new architecture. The remaining work is operator-experience, validation, and resolving one structural tension (below).

### The settingSources=[] Tension

`#11` set `settingSources: []` on `general-purpose` and `explorer` bundled roles to suppress CLAUDE.md loading and save tokens. But the SDK's skill-discovery path needs `setting_sources=["user","project"]` to find SKILL.md files. The SDK only auto-injects when `setting_sources is None` — an explicit `[]` blocks both CLAUDE.md *and* skill discovery.

**Implication**: a role that wants skills must EITHER:
- Leave `settingSources` unset (None) → SDK auto-injects `["user","project"]`, both CLAUDE.md AND skills load
- Set `settingSources: ["user", "project"]` explicitly → same effect, both load
- Set `settingSources: []` → skill list is sent on the wire but no SKILL.md files are discoverable; CLI silently has nothing to invoke

This is a real correctness trap operators will hit. The bundled `general-purpose` role currently has `settingSources: []`; if an operator adds `skills: [crew-showcase]` to it, the result is a teammate that thinks it has the skill but can't run it. **Validation must catch this at pack-load time.**

### Success Criteria

- [ ] **SC-1a (stub-mode contract)**: A role pack file declaring `skills: [foo, bar]` in YAML frontmatter results in `ClaudeAgentOptions.skills == ["foo", "bar"]` reaching the SDK boundary at spawn time. *Verifiable via*: deterministic unit/integration test asserting at the factory edge — captures the kwargs passed to `ClaudeSDKClient`/`ClaudeAgentOptions`. Runs in CI under stub mode.
- [ ] **SC-1b (live dogfood probe)**: An operator-run live probe (gated by `CLAUDE_CREW_LIVE_TESTS=1`) spawns a teammate with a real skill declared, sends a message that triggers it, and observes transcript records of the skill body executing. *Verifiable via*: manual gate at Phase 5; not CI-runnable.
- [ ] **SC-2**: A role pack file declaring `skills: all` is accepted by the loader, propagated to `ClaudeAgentOptions.skills == "all"`, and the teammate has access to every skill discoverable in user + project skill dirs. Includes the contract that `parse_pack_text` returns `(key, AgentDefinition, PackFrontmatter, raw_body)` with `AgentDefinition.skills == "all"` (the SDK's `Literal["all"]` form). *Verifiable via*: unit test on the loader contract; subsumes former SC-8.
- [ ] **SC-3**: A role pack file declaring a non-empty `skills:` list (or `skills: all`) AND an **explicit empty list** `settingSources: []` is REJECTED at pack-load time with a clear error pointing the operator at the contradiction. **Omitting `settingSources` entirely is accepted** (the SDK then auto-injects `["user","project"]`). The parametrized test MUST cover three shapes: (a) `skills=[foo], settingSources=[]` → reject; (b) `skills=[foo], settingSources` absent → accept; (c) `skills=[foo], settingSources=["user","project"]` → accept. *Verifiable via*: unit test on `_validate_frontmatter`.
- [ ] **SC-4**: A role pack file declaring `skills: [foo]` where `foo` does not exist as a discoverable SKILL.md in user or project skill dirs is logged as a WARN at pack-load time but does not raise. The WARN must reach the same logger sink as the existing `_user_loader` shadow-log INFOs (so it surfaces in the same place operators already look). *Verifiable via*: unit test asserts WARN log + successful pack load.
- [ ] **SC-5**: An invalid `skills` value (non-list and non-`"all"` string, or list element non-string) raises `PackLoadError` with a clear message. The empty-list case `skills: []` is treated as a **no-op** (effectively equivalent to omitting `skills`) — accepted, no validation interaction with `settingSources`, no skills propagated. *Verifiable via*: unit test parametrized over malformed shapes including the explicit empty-list no-op case.
- [ ] **SC-6 (bundled `general-purpose` gets `skills: all`)**: The bundled `general-purpose` role declares `settingSources: ["user", "project"]` AND `skills: all`. `explorer` and `planner` keep their current narrow shape (no skills declared) — they're leaf-shaped utility roles where skill invocation isn't load-bearing. Rationale (resolves former OQ-1; co-architect pushback considered and rejected): the #24 fail-soft analogy doesn't fit — #24 is about *misrouting* (shell-task to non-Bash role), whereas skills are *exposure* (the LLM decides to invoke or not, identical to any other tool). Vision row #23 explicitly says "invocable by SDK teammates the same way the lead invokes them" — narrow defaults ship the mechanism but not the parity, and operators would have to override every bundled role to get the thing the vision promised. The genuine concern (A-1: skills carrying their own tool surface) is defended at the observability layer (PHASE-2-OBS) rather than by neutering the default. *Verifiable via*: assertion that bundled `general-purpose.md` frontmatter has `skills: all` and `settingSources: ["user", "project"]`, factory output shows `ClaudeAgentOptions.skills == "all"` for general-purpose; and that `explorer.md`/`planner.md` retain their current frontmatter unchanged.
- [ ] **SC-7 (cascade — list over list)**: The user/project pack discovery cascade (`build_merged_pack`) treats `skills` like every other PackFrontmatter field — user pack overrides default, project pack overrides user, whole-key replacement. *Verifiable via*: unit test fixture with a default role declaring `skills: [a]`, user override declaring `skills: [b]`, asserting merged role has `skills: [b]`.
- [ ] **SC-8 (cascade — "all" collapse)**: The cascade also handles the `"all" ↔ list` shape transition cleanly. Default declares `skills: all`, user override declares `skills: [foo]` → merged role has `skills: [foo]` (override wins, "all" is replaced not unioned). And the symmetric case: default declares `skills: [foo]`, user override declares `skills: all` → merged role has `skills: "all"`. *Verifiable via*: parametrized unit test on the cascade.
- [ ] **SC-9**: Documentation (`README.md` or `CLAUDE.md`) describes: (a) how to add `skills:` to a role pack with a complete example; (b) the `settingSources: []` validation error and how to fix it; (c) the cwd-trap for project skills (A-7) — operators must launch from the project root; (d) **the literal stderr line format the WARN produces** so operators know what to grep for; (e) a one-line note that startup WARNs do not reach the dashboard today (PHASE-2-OQ-1) and a BACKLOG follow-up exists for surfacing them. *Verifiable via*: manual doc review + presence of all five elements (a-e); BACKLOG entry filed by Phase 5.
- [ ] **SC-10**: No regression in #11's lightweight-context behavior. `explorer` and `planner` bundled roles retain their current `settingSources` (`[]` and `[project]` respectively) and prompt sizes. *Verifiable via*: the existing #11 prompt-size assertion holds.

### Questions

- [x] **How does the SDK expose skills?** → Native `ClaudeAgentOptions.skills` and `AgentDefinition.skills`. Plumbing already wired in claude-crew.
- [x] **Does `setting_sources` auto-load skills?** → Only if `setting_sources is None`. Explicit `[]` blocks discovery (the #11 trap).
- [x] **Is `Literal["all"]` accepted at the type level?** → Yes by SDK. PackFrontmatter must be extended to allow the string form alongside the tuple.
- [x] **Should the bundled `general-purpose` get `skills: all` by default?** → **Resolved at SC-6**: YES. Initial co-architect pushback (different trust shape from lead) was reconsidered: skills are *exposure*, not *routing* — the LLM-invocation shape is identical to any other tool, just like in the lead session. The #24 analogy is about misrouting, which is a different pathology. Vision row #23 explicitly claims parity-of-invocation. Narrow defaults would ship the mechanism dark. The genuine A-1 concern is defended via PHASE-2-OBS observability, not by neutering the default.
- [x] **How to surface "skill foo not found"?** → **Resolved at SC-4**: WARN-only at pack-load, no FAIL. Skill dirs may legitimately not be present at pack-load but exist at runtime; FAIL-fast would block forward-compat. Where the WARN reaches a running operator is **deferred to Phase 2 OQ-1** (transcript? broker envelope? stderr only?).
- [ ] **Where do pack-load WARNs surface to a running operator?** → *Phase 2 OQ-1.* Today they go to the broker's logger; whether that reaches the dashboard or only stderr is an observability question for Phase 2.

### Phase 1 Assumptions (default-accept)

- **A-1 (RESOLVED via empirical probe, 2026-05-01)**: Role-level `disallowedTools` **DOES cascade into skill body tool calls.** A skill body executes within the role's resolved tool set — it cannot invoke a tool the role disallows. Probe: a temporary `disallow-probe` skill declaring it would run Bash to write a marker file, invoked from a session with `disallowed_tools=["Bash"]` and `allowed_tools=["Bash","Read"]`. The skill body reported "Bash is not in my currently loaded tool set"; marker file not written. Confirms the structural inference from the SDK source spike: `uc()` (the CLI tool resolver) filters the merged pool (role tools ∪ skill tools) by `disallowedTools` once at agent spawn. No escalation surface. *Implication*: Phase 2 PHASE-2-OBS observability matrix is informational, not a hard validation requirement. Operators authoring `disallowedTools` retain their intended security posture even with `skills: all`.
- **A-2**: Skills declared on a role pack are advisory at pack-load time — the loader does not require SKILL.md files to exist (SC-4). The skill discovery happens in the CLI subprocess at runtime; pack-load only validates the shape of the declaration. *Default*: WARN at pack-load if not found, do not FAIL.
- **A-3**: Bundled `general-purpose` ships with `skills: all` and `settingSources: ["user", "project"]` (SC-6 resolved former OQ-1). `explorer` and `planner` retain their narrow shape — they're utility roles where skill invocation isn't load-bearing. Operators who want narrower defaults override via `~/.claude/agents/general-purpose.md`. *Default*: accept parity-of-invocation as the right shipped default; the cascade lets operators tighten if they choose.

### Phase 2 Carry-Forward Notes

- **PHASE-2-OBS**: When a role declares both `disallowedTools` and `skills`, pack-load should emit an INFO listing which discoverable skills declare tools that intersect the disallow list. Not a reject, not a warn — pure observability so the operator sees the matrix they authored. (Co-architect Q2 input. Cheap, additive, surfaces the silent escalation vector without enforcing.)
- **PHASE-2-OQ-1**: Where do pack-load WARNs (SC-4) reach a running operator — broker envelope/dashboard, transcript, or stderr only? Today they go to the broker logger; observability surface is undecided.

### Constraints & Dependencies

- **Requires**: `claude-agent-sdk >= 0.1.68` (already pinned). `PackFrontmatter` infrastructure from #10. `settingSources` validation from #11. Bundled-pack frontmatter pattern from #21.
- **Affected modules**: `claude_crew/subagents/_loader.py` (validation), `claude_crew/subagents/_user_loader.py` (accepted-keys set), `claude_crew/subagents/general_purpose.md` (frontmatter update), and tests.
- **Breaking changes**: No. Additive: a role that doesn't declare `skills` continues to behave exactly as today (no skills surface). Bundled `general-purpose` frontmatter changes — needs explicit retention of #11's lightweight-context test.
- **Performance implications**: None. Skill discovery happens in the CLI subprocess, which already runs.
- **Security implications**: Skills can have arbitrary `tools:` in their own frontmatter. A teammate with `skills: all` inherits whatever those skills declare. The role pack's `disallowedTools` does NOT cascade into a skill's tool surface (skills are a separate scope in the SDK CLI). **Surface as Assumption in Phase 2** — operator must trust the skills they install.
- **Cross-feature interactions**:
  - **#11 (lightweight subagent context)**: direct tension. Resolved via SC-3 + SC-6.
  - **#15 (reviewer + runner pack)**: the reviewer role is the natural user of `/security-review`, the runner role is a natural user of test/runner skills. #23 should land first so #15's pack files can declare skills out of the box.
  - **#17 (PackFrontmatter parity)**: orthogonal. #17 adds `mcpServers`, `permissionMode` mirror cleanup, `memory`. No conflict.
  - **#24 (subagent dispatch guard)**: orthogonal but adjacent — both touch what a teammate can or cannot do at spawn time. No direct dependency.

### Cross-Feature Reading Check

Files that read `PackFrontmatter.skills` or `AgentDefinition.skills`:
- `claude_crew/subagents/_loader.py:42, 124-125` — declares + maps to AgentDefinition
- `claude_crew/sdk_teammate.py:891-898` — extracts and routes to ClaudeAgentOptions
- (No other consumers in src; tests touch via fixtures only)

Files that read `settingSources`:
- `claude_crew/subagents/_loader.py:45, 177-179` — declared + validated
- `claude_crew/subagents/_user_loader.py` — threads through cascade
- `claude_crew/factories.py` — passes role_ss into sdk_factory closure
- `claude_crew/sdk_teammate.py` — passes to ClaudeAgentOptions

The intersection — a role with both `settingSources` and `skills` declared — has no current consumer and no validation. SC-3 closes that gap.

**Gate**: Sentinel review of these SCs is the next step before user confirmation.

---

## Phase 2: Design & Specification

### Architecture Overview

Most of the wire path is already shipped (#10 added `PackFrontmatter.skills`; SDK 0.1.68 natively exposes `ClaudeAgentOptions.skills` and `AgentDefinition.skills`). #23's design is a focused four-part change:

1. **Type extension**: `PackFrontmatter.skills` accepts the SDK's `Literal["all"]` form alongside the tuple form.
2. **Validation**: pack-load rejects the `skills + settingSources=[]` conflict; emits WARN for skill names not discoverable in user/project skill dirs.
3. **Bundled defaults**: `general-purpose.md` flips to `settingSources: ["user", "project"]` and `skills: all` for parity-of-invocation with the lead session.
4. **Documentation**: README/CLAUDE.md note explaining the operator opt-in pattern and the `settingSources` interaction.

The `skills` field already lives on `AgentDefinition` (not on the `PackFrontmatter`-only side-channel), so cascade replacement via `merge_packs` is free — no `role_skills` parallel dict needed (asymmetric with `role_ss`; this asymmetry is intentional and worth a one-line code comment).

No new modules. No new IPC. No teammate-spawn-time work. The change footprint is `_loader.py`, `_user_loader.py`, `general_purpose.md`, and tests.

### Data / API Contracts

**Type extension** (`claude_crew/subagents/_loader.py`):

`_loader.py` today imports `from typing import Any` only. The `Literal` symbol must be ADDED to that import to support the new field type (sentinel MF-1):

```python
from typing import Any, Literal
```

Then:

```python
@dataclass(frozen=True)
class PackFrontmatter:
    ...
    skills: tuple[str, ...] | Literal["all"] | None = None  # was: tuple[str, ...] | None
    ...
```

**Validation in `_validate_frontmatter`** — replace the existing line ~196-198:

```python
# Existing:
# skills=(tuple(str(s) for s in d["skills"]) if d.get("skills") is not None else None),

# New:
raw_skills = d.get("skills")
if raw_skills is None:
    parsed_skills = None
elif isinstance(raw_skills, str):
    if raw_skills != "all":
        raise PackLoadError(
            f"pack file {path}: skills string value must be 'all'; got {raw_skills!r}"
        )
    parsed_skills = "all"
elif isinstance(raw_skills, list):
    if not all(isinstance(s, str) for s in raw_skills):
        raise PackLoadError(
            f"pack file {path}: skills list elements must be strings"
        )
    parsed_skills = tuple(raw_skills)  # empty tuple OK — treated as no-op (SC-5)
else:
    raise PackLoadError(
        f"pack file {path}: skills must be a list of strings or the string 'all'; "
        f"got {type(raw_skills).__name__}"
    )
```

**SC-3 validator** — added to `_validate_frontmatter` before constructing `PackFrontmatter`:

```python
# Reject the silent-misconfig trap: declaring skills with explicit settingSources=[]
# would send Skill(name) on the wire but block SKILL.md discovery, so the teammate
# silently has nothing to invoke. Note: settingSources None is OK (SDK auto-injects
# ["user","project"]); only the explicit empty-list case is the conflict.
ss_explicit_empty = ss is not None and len(ss) == 0
skills_active = parsed_skills is not None and parsed_skills != ()  # empty tuple = no-op
if skills_active and ss_explicit_empty:
    raise PackLoadError(
        f"pack file {path}: declaring skills with settingSources=[] (explicit empty list) "
        f"is contradictory — skills are sent on the wire but SKILL.md discovery is blocked. "
        f"Either omit settingSources (SDK auto-injects ['user','project']), or set it explicitly."
    )
```

**Validator construct call** — the existing `_validate_frontmatter` ends by calling `PackFrontmatter(...)` with `skills=tuple(str(s) for s in d["skills"]) if d.get("skills") is not None else None` inline (line 196-198). **This inline expression MUST BE REPLACED** with `skills=parsed_skills` so the new validation logic above is the only path. Leaving the inline expression intact would make `skills: "all"` re-try `tuple(str(s) for s in "all")` → `("a","l","l")`. Phase 3 task description must call this out explicitly.

**`parse_pack_text` mapping** — replace the existing line 124-125:

```python
# Existing:
# if fm.skills is not None:
#     agent_kwargs["skills"] = list(fm.skills)

# New:
if fm.skills is not None:
    if isinstance(fm.skills, str):
        agent_kwargs["skills"] = fm.skills  # "all" passes through as the SDK Literal
    elif fm.skills:  # non-empty tuple
        agent_kwargs["skills"] = list(fm.skills)
    # else: empty tuple — treat as no-op, do not set agent_kwargs["skills"]
```

**SC-4 WARN locus** — in `_user_loader.py`, **inside `build_merged_pack`, immediately after `merge_packs` resolves and BEFORE the `return` statement**. The exact call site (sentinel MF-2):

```python
def build_merged_pack(...) -> tuple[dict[str, AgentDefinition], dict[str, list[str] | None], dict[str, str]]:
    default, default_ss, default_bodies = ...
    user, user_ss, user_bodies = discover_dir(...)
    project, project_ss, project_bodies = discover_dir(...)
    role_ss = {**default_ss, **user_ss, **project_ss}
    bodies = {**default_bodies, **user_bodies, **project_bodies}
    merged = merge_packs(merge_packs(default, user), project)
    _warn_unknown_skills(merged)        # <-- inserted here, on the dict (not the tuple)
    return merged, role_ss, bodies
```

Helper signature: `_warn_unknown_skills(merged: dict[str, AgentDefinition]) -> None`. Walks each role's `AgentDefinition.skills`; compares against names discoverable in `~/.claude/skills/*/SKILL.md` and `<cwd>/.claude/skills/*/SKILL.md`; logs a WARN for each unknown name. The `"all"` case and `None` case skip the check.

```python
def _discover_skill_names() -> set[str]:
    """Walk user + project skill dirs, return the set of skill names found.

    A skill is identified by the immediate subdirectory under .claude/skills/
    containing a SKILL.md file (the directory name is the skill name).
    Best-effort: missing dirs treated as empty.
    """
    names: set[str] = set()
    for base in (Path.home() / ".claude" / "skills",
                 Path.cwd() / ".claude" / "skills"):
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "SKILL.md").is_file():
                names.add(child.name)
    return names

def _warn_unknown_skills(merged_pack: dict[str, AgentDefinition]) -> None:
    discovered = _discover_skill_names()
    for role, agent in merged_pack.items():
        skills = getattr(agent, "skills", None)
        if skills is None or skills == "all":
            continue
        unknown = [s for s in skills if s not in discovered]
        if unknown:
            logger.warning(
                "agent %r declares unknown skills %s — not found in user or project "
                "skill dirs at startup; teammate will fail to invoke them at runtime",
                role, unknown,
            )
```

**Bundled `general-purpose.md` frontmatter update**:

```yaml
---
description: Catch-all assistant for shaped work — find, read, write, search, edit, fetch.
model: sonnet
tools: [Read, Grep, Glob, Edit, Write, WebFetch, WebSearch]
effort: medium
maxTurns: 20
background: false
settingSources: ["user", "project"]   # was: []
skills: all                            # NEW
---
```

`explorer.md` and `planner.md` frontmatter unchanged.

### Design Decisions

- **D-1 (Type extension shape)**: `PackFrontmatter.skills: tuple[str, ...] | Literal["all"] | None`. Union at the dataclass level; `_validate_frontmatter` arbitrates string-vs-list. Rejected: sentinel-string encoding (opaque to readers); dual-field `skills` + `skills_all` (doubles validation surface for zero gain). *Carried into:* `PackFrontmatter` field declaration in `_loader.py:42`; `parse_pack_text` `isinstance(fm.skills, str)` branch; test `test_skills_all_form_accepted`.

- **D-2 (Empty-list no-op)**: `skills: []` (explicit empty list) is accepted at validation but does NOT set `agent_kwargs["skills"]`, so `AgentDefinition.skills` ends up `None`. Rationale: zero entries means "no skills" — no semantic difference from omitting the key, no contradiction with `settingSources=[]`. Avoids a special-case rejection that would surprise. *Carried into:* `parse_pack_text` `elif fm.skills:` guard (truthy check skips empty tuple); test `test_skills_empty_list_is_noop`.

- **D-3 (SC-3 validator scope)**: The `skills + settingSources=[]` conflict check fires ONLY for "active" skills declarations — non-empty list or `"all"`. `skills: []` paired with `settingSources: []` is permitted (no-op + no-source = consistent). *Carried into:* `_validate_frontmatter` `skills_active and ss_explicit_empty` guard; test `test_skills_empty_with_settingsources_empty_accepted`.

- **D-4 (WARN locus is `_user_loader`)**: SC-4 discovery check lives in `_user_loader._warn_unknown_skills`, called from `build_merged_pack` after the cascade resolves. Rationale (per co-architect Pushback 2): pack-load validator is path-agnostic and shouldn't walk filesystem; spawn-time check is noisy; SDK CLI's runtime warning is invisible to the operator. `_user_loader` is already the place that walks dirs and emits WARN/INFO at startup-freeze time — the new WARN slots in next to existing shadow-log machinery. *Carried into:* `_user_loader._warn_unknown_skills` definition; `build_merged_pack` call site; test `test_unknown_skill_warns_at_pack_load`.

- **D-5 (Skill discovery scope = user + project, two paths)**: `_discover_skill_names` walks `~/.claude/skills/` and `<cwd>/.claude/skills/` only. Mirrors the agents discovery cascade. SDK's `_apply_skills_defaults` injects `setting_sources=["user","project"]` — same scope. Local (single-file `.claude/settings.local.json`) is NOT a skill source by design. *Carried into:* `_discover_skill_names` two-path iteration; test `test_discover_skill_names_user_and_project`.

- **D-6 (Cascade is free, asymmetric with role_ss)**: SC-7/SC-8 fall out of `merge_packs` whole-key replacement on `AgentDefinition`. NO `role_skills` side-channel needed (asymmetric with `role_ss`, which exists because `settingSources` is on `PackFrontmatter` only). A code comment in `_user_loader.build_merged_pack` near the `merge_packs` call documents the asymmetry; the comment text MUST mention `role_ss` by name so a future reader grepping for `role_ss` lands on it. *Carried into:* code comment with literal `role_ss` token near `merge_packs` call; test `test_skills_cascade_user_overrides_default`; test `test_skills_cascade_all_to_list_collapse`; test `test_skills_cascade_user_empty_list_removes_default` (D-2 cascade-removal path).

- **D-7 (Bundled `general-purpose` defaults to `skills: all`)**: Resolved former OQ-1. Vision row #23 is parity-of-invocation; A-1 probe confirmed `disallowedTools` cascades into skill bodies, so the security posture is preserved. `explorer` and `planner` keep narrow shape — utility roles where skill invocation isn't load-bearing. *Carried into:* `general_purpose.md` frontmatter; test `test_bundled_general_purpose_has_skills_all`; test `test_bundled_explorer_planner_skills_unset`.

- **D-8 (No `Literal["all"]` cascade-collapse special case)**: When user override has `skills: [foo]` and default has `skills: all`, the merged result is `skills: ("foo",)` — whole-key replacement. The opposite is symmetric. No "merge "all" with list" semantics; if the operator wants `all`, they declare `all`. *Carried into:* test `test_skills_cascade_all_to_list_collapse`; test `test_skills_cascade_list_to_all_collapse`.

- **D-9 (`AgentDefinition.skills == "all"` passes through to SDK as the string)**: The SDK's `ClaudeAgentOptions.skills` field accepts `Literal["all"]`. `sdk_teammate.py:893-896` does `getattr(role_def, "skills", None)` and assigns to `opts_kwargs["skills"]` without type coercion — already correct. No code change needed in `sdk_teammate.py`. *Carried into:* existing `sdk_teammate.py:891-898` block (no edit); factory test `test_skills_all_passes_through_sdk_teammate_factory` asserting the `"all"` string survives `getattr` → `opts_kwargs` → `ClaudeAgentOptions` for `general-purpose`.

- **D-10 (PHASE-2-OBS observability matrix is not in scope)**: Per A-1 probe outcome (no escalation), the disallowedTools×skills observability matrix is informational, not a hard validation. Defer to post-#23 if operators surface confusion. *Carried into:* BACKLOG entry on close; no code in this feature.

### Edge Cases

- **Empty `skills: []`**: accepted, no-op, no AgentDefinition.skills set. (D-2)
- **`skills: all` (string)**: accepted, propagates as `Literal["all"]`. (D-1)
- **`skills: ["all"]` (single-element list with literal "all")**: parsed as `tuple` `("all",)` — sent on wire as `Skill(all)`, which the SDK CLI will likely warn about as "skill 'all' not found." This is a benign operator typo; SC-4 WARN catches it at pack-load.
- **`skills: [42, "foo"]` (mixed types)**: rejected at validation per D-1 (`all(isinstance(s, str))`).
- **`skills: "All"` (wrong case)**: rejected — only the literal lowercase `"all"` is accepted.
- **`skills: {"foo": "bar"}` (dict)**: rejected — falls through the type check to the final `raise`.
- **Duplicate names `skills: [foo, foo]`**: tuple preserves duplicates; SDK CLI is the source of truth for whether duplicates are coalesced. Not validated.
- **User override with `skills: null` (explicit YAML null)**: parsed as `None`, treated as "no skills declared on this override" — but `merge_packs` still replaces the whole `AgentDefinition`, so the override role gets `skills=None` and inherits nothing from default. This matches `disallowedTools` and other override semantics.
- **User override with `skills: []` to REMOVE default skills**: explicit empty list at the user level. D-2 says empty list is no-op → `agent_kwargs["skills"]` not set → `AgentDefinition.skills=None` on the override role. `merge_packs` whole-key replaces, so the merged role's `skills` is `None`. Operator successfully removes the default's skills. *This is the obvious operator gesture and must be tested* (`test_skills_cascade_user_empty_list_removes_default`).
- **Skill name with non-identifier chars**: passed through as-is; SDK CLI is the source of truth for valid skill names. Not validated.
- **Skill dirs missing entirely**: `_discover_skill_names` returns empty set; any declared `skills: [foo]` produces a WARN, no exception.
- **Skill dir present but has no SKILL.md**: that subdir is not counted as a skill (D-5 explicitly checks for `SKILL.md`).
- **Symlinks in skill dirs**: `is_dir()` follows symlinks by default; `is_file()` likewise. Symlinked skills work transparently.

### Specification

The full implementation diff covers: extending one type, replacing ~10 lines of validation, adding two helpers (`_discover_skill_names`, `_warn_unknown_skills`), updating one bundled pack file, and adding ~12 tests. No SdkTeammate changes. No factory changes. No broker, MCP, or UI changes.

After this feature, an operator workflow looks like:

1. Drop a skill at `~/.claude/skills/my-skill/SKILL.md`.
2. (Optional) Author or override an agent at `~/.claude/agents/my-role.md` with `skills: [my-skill]` (or `skills: all`).
3. Spawn a teammate via `mcp__claude-crew__spawn_teammate(role="my-role", ...)`.
4. The teammate can invoke `/my-skill` the same way the lead session does.

For the bundled `general-purpose` role: skills work out of the box with `skills: all`. No operator action needed unless they want to narrow.

### Phase 2 Assumptions

- **A-4**: SDK behavior for `skills="all"` (the string form) is stable across SDK minor versions in the 0.1.x series. The SDK type signature `Literal["all"]` is documented; we depend on it. *Default*: pin `claude-agent-sdk >= 0.1.68` continues to bound the behavior.
- **A-5**: The skill discovery filesystem walk (`_discover_skill_names`) is fast enough at server-startup time — typical operator has <50 skills total. No caching beyond the single startup pass needed. *Default*: walk on every `build_merged_pack` call (which is once at server start).
- **A-6**: Operators editing `~/.claude/skills/` after server startup do NOT get a re-walk — the WARN reflects state-at-startup. This matches existing `~/.claude/agents/` cascade-freeze semantics. *Default*: accept; document in SC-9.
- **A-7 (cwd trap for project skill discovery)**: `_discover_skill_names` resolves the project skill dir via `Path.cwd()` at server-startup time. If the operator launches `claude-crew` from a directory other than the project root, project skills are silently NOT discovered, and project-only skill names produce spurious WARNs. This is consistent with the existing `~/.claude/agents/` project-cascade resolution today. *Default*: accept the cwd-relative resolution; SC-9 docs note "launch claude-crew from the project root for project-skill discovery to work."
- **A-8 (PermissionError on skill dir is not handled)**: `_discover_skill_names` does NOT wrap `iterdir()` in a try/except. If `~/.claude/skills/` exists but is not readable (e.g., `chmod 000`), the server crashes at pack-load time. This is consistent with `discover_dir` in `_user_loader.py` for the agent dirs today (also unprotected). *Default*: accept the crash. Low real-world probability; if it surfaces, address symmetrically across both loaders in a follow-up.
- **A-9 (case-insensitive filesystem collisions)**: On case-insensitive filesystems (macOS HFS+/APFS default, NTFS via WSL mounts), declared `skills: [Foo]` against a dir named `foo/` would produce a spurious unknown-skill WARN — the discovered set contains `foo`, not `Foo`, and the comparison is case-sensitive. *Default*: accept; document in SC-9 if needed. Linux-native operators are unaffected.

### Phase 2 Open Questions

- [x] **OQ-1 (RESOLVED 2026-05-01)**: Startup-time WARNs go to stderr for #23. Dashboard surfacing is **explicitly out of scope** and tracked as a new vision pipeline row (#25 — startup diagnostics on dashboard). SC-9 docs include the literal stderr line format so operators can grep. BACKLOG entry filed at Phase 5 referencing #25.

### Pre-existing Tests That Must Be Updated in Phase 3

Sentinel MF-3: the bundled-pack frontmatter change is a regression-guard that other tests assert against. These must be updated AS PART OF the bundled-defaults task (not in a "fix tests" cleanup pass):

- `tests/test_user_loader.py:527` — `test_bundled_packs_have_expected_setting_sources`. Currently asserts `role_ss.get("general-purpose") == []`. Must update to `role_ss.get("general-purpose") == ["user", "project"]` and add `assert merged_pack["general-purpose"].skills == "all"`.
- Any other test asserting the literal current `general_purpose.md` frontmatter or prompt size — Phase 3 task description must call out a `grep -rn "general-purpose" tests/` audit before declaring the task complete.

### Phase 3 Test-List Additions (sentinel + co-architect notes)

Beyond the tests already named in carried-into pointers, Phase 3 must include:

- `test_skills_invalid_shape_raises` parametrized cases: `""` (empty string), `" all "` (whitespace-padded), `"All"` (wrong case), `42` (non-string non-list), `{"foo": "bar"}` (dict). All raise `PackLoadError`.
- `test_skills_cascade_user_empty_list_removes_default` — D-2 cascade-removal path (default `skills: [a,b]`, override `skills: []`, merged `skills=None`).
- `test_skills_all_passes_through_sdk_teammate_factory` — D-9 round-trip assertion that the literal string `"all"` survives `getattr(role_def, "skills", None)` → `opts_kwargs["skills"]` → `ClaudeAgentOptions.skills`. Asserts in factory test plane against `general-purpose`.
- SC-9 doc verification — Phase 3 task must produce a literal stderr line example (e.g., `WARNING claude_crew.subagents.loader: agent 'reviewer' declares unknown skills ['security-review'] — not found in user or project skill dirs at startup; teammate will fail to invoke them at runtime`) and place it verbatim in the README/CLAUDE.md so operators can grep for it.

### Cross-Feature Reading Check (Phase 2 round)

Re-confirmed during Phase 2 synthesis:

- `sdk_teammate.py:891-898` does `getattr(role_def, "skills", None)` and routes to `ClaudeAgentOptions.skills`. **No edit needed** — the union shape `tuple | str | None` flows through `getattr` cleanly because the SDK's `ClaudeAgentOptions.skills: list[str] | Literal["all"] | None` accepts both.
- `factories.default_factory` does NOT need to read `role_skills` from a side-channel; D-6 keeps skills as an `AgentDefinition` attribute. **No edit.**
- `_user_loader._ACCEPTED_FRONTMATTER_KEYS = frozenset(PackFrontmatter.__dataclass_fields__)` — `skills` is already accepted (no spurious WARN from `strict_parse`). **No edit.**

The footprint is honestly tiny.

**Gate**: co-architect review of Phase 2, then sentinel review of Phase 2, then user OQ-1 resolution, then Phase 3.

---

## Phase 3: Task Breakdown

5 tasks. Tests written first (red), then implementation (green). Verification commands shown for each task.

---

### Task 1: PackFrontmatter type extension + validation

**Depends on**: None | **Blocks**: T2, T3, T4

**Scope**:
- Add `Literal` to the existing `from typing import Any` in `claude_crew/subagents/_loader.py` (sentinel MF-1).
- Extend `PackFrontmatter.skills` field type to `tuple[str, ...] | Literal["all"] | None`.
- Replace the inline `skills=tuple(...)` expression in `_validate_frontmatter` (line ~196-198) with the new `parsed_skills` block that handles three shapes: `None`, `"all"` string (D-1), list-of-strings (validated per-element). Reject `""`, `" all "`, `"All"`, non-string non-list values, and lists with non-string elements (sentinel M-1).
- Add the SC-3 conflict validator: reject when `parsed_skills` is active (non-empty list or `"all"`) AND `settingSources` is an explicit empty list. `parsed_skills == ()` (empty tuple no-op, D-2) does NOT trigger the conflict.
- Update `parse_pack_text` (line 124-125) to map `fm.skills` to `agent_kwargs["skills"]` per D-1: `"all"` passes through as the string; non-empty tuple → `list(...)`; empty tuple skipped.

**Acceptance Criteria** (BDD scenarios):

```
Scenario: skills "all" form accepted
  Given a pack file with frontmatter "skills: all"
  When parse_pack_text runs
  Then PackFrontmatter.skills == "all" (the string, not a tuple)
  And AgentDefinition.skills == "all"

Scenario: skills list form accepted
  Given a pack file with frontmatter "skills: [foo, bar]"
  When parse_pack_text runs
  Then PackFrontmatter.skills == ("foo", "bar")
  And AgentDefinition.skills == ["foo", "bar"]

Scenario: skills empty list is no-op
  Given a pack file with frontmatter "skills: []"
  When parse_pack_text runs
  Then PackFrontmatter.skills == ()
  And AgentDefinition.skills is None

Scenario: skills + settingSources=[] rejected
  Given a pack file with frontmatter "skills: [foo]" AND "settingSources: []"
  When parse_pack_text runs
  Then PackLoadError raised with message mentioning "contradictory"

Scenario: skills empty + settingSources=[] accepted
  Given a pack file with frontmatter "skills: []" AND "settingSources: []"
  When parse_pack_text runs
  Then no exception (consistent: no-op + no-source = consistent)

Scenario: skills omitted + settingSources=[] accepted
  Given a pack file with frontmatter "settingSources: []" and no skills key
  When parse_pack_text runs
  Then no exception

Scenario: skills with explicit ["user","project"] settingSources accepted
  Given a pack file with "skills: [foo]" AND "settingSources: [user, project]"
  When parse_pack_text runs
  Then no exception, AgentDefinition.skills == ["foo"]

Scenario: malformed skills shapes rejected
  Given pack files with skills set to one of: "", " all ", "All", 42, {"foo":"bar"}, [42, "foo"], [None]
  When parse_pack_text runs
  Then PackLoadError raised for each shape
```

**Tests** (`tests/test_pack_loader.py`):
- `test_skills_all_form_accepted` (D-1)
- `test_skills_list_form_accepted` (existing-shape regression)
- `test_skills_empty_list_is_noop` (D-2)
- `test_skills_with_settingsources_empty_rejected` (D-3, SC-3)
- `test_skills_empty_with_settingsources_empty_accepted` (D-3 nuance)
- `test_skills_omitted_with_settingsources_empty_accepted` (SC-3 nuance)
- `test_skills_with_explicit_user_project_settingsources_accepted` (SC-3 nuance)
- `test_skills_invalid_shape_raises` parametrized over `["", " all ", "All", 42, {"foo":"bar"}, [42], [None]]` (sentinel M-1, SC-5)

**Verification**: `uv run pytest tests/test_pack_loader.py -k skills` — all pass.

---

### Task 2: Skill-discovery WARN at pack-load

**Depends on**: T1 | **Blocks**: T4 (cascade tests touch the warn path)

**Scope**:
- In `claude_crew/subagents/_user_loader.py`, add `_discover_skill_names() -> set[str]` walking `~/.claude/skills/` and `<cwd>/.claude/skills/` for subdirs containing a `SKILL.md`. No try/except on `iterdir()` (A-8 accepted).
- Add `_warn_unknown_skills(merged: dict[str, AgentDefinition]) -> None` walking `merged.values()`, skipping `None` and `"all"`, comparing each list-form name against `_discover_skill_names()`, logging WARN via the existing `claude_crew.subagents.loader` logger (sentinel M-4: log line documented in SC-9 doc task).
- Insert call site in `build_merged_pack` exactly per Phase 2 spec — between `merge_packs` resolution and `return` (sentinel MF-2).
- Add D-6 code comment near `merge_packs` call site mentioning `role_ss` by name verbatim: `# Skills cascade via AgentDefinition (unlike settingSources which uses role_ss side-channel — see discover_dir).`

**Acceptance Criteria**:

```
Scenario: declared skill not found in any skill dir → WARN
  Given ~/.claude/skills/ contains skill "foo" (with SKILL.md)
  And no project skill dir exists
  And a user agent declares "skills: [foo, bar]" (bar is not on disk)
  When build_merged_pack runs
  Then a WARN log is emitted naming "bar" and the role
  And no exception is raised
  And the merged pack contains the role with skills = ("foo", "bar")

Scenario: skills "all" skips the discovery check
  Given a user agent declares "skills: all"
  When build_merged_pack runs
  Then no WARN is emitted for that role (regardless of skill dir contents)

Scenario: project skill dir discovered when launched from project root
  Given <cwd>/.claude/skills/ contains skill "proj-skill"
  And user agent declares "skills: [proj-skill]"
  When build_merged_pack runs
  Then no WARN is emitted

Scenario: subdir without SKILL.md is not a skill
  Given ~/.claude/skills/empty-dir/ exists with no SKILL.md inside
  When _discover_skill_names runs
  Then "empty-dir" is NOT in the returned set
```

**Tests** (`tests/test_user_loader.py`):
- `test_discover_skill_names_user_and_project` — fixture creates both dirs in tmp_path; monkeypatch home and chdir; assert names returned (D-5)
- `test_discover_skill_names_skips_subdir_without_skillmd`
- `test_unknown_skill_warns_at_pack_load` — caplog assertion on the logger (D-4)
- `test_skills_all_skips_unknown_check` — vacuity defense (D-4)
- `test_warn_message_contains_role_and_skill_name` — defends SC-9 doc grep target

**Verification**: `uv run pytest tests/test_user_loader.py -k "skill"` — all pass; `uv run pytest tests/test_user_loader.py` — full file passes (no regression).

---

### Task 3: Bundled `general-purpose` defaults flip + existing-test update

**Depends on**: T1 | **Blocks**: T4

**Scope**:
- Update `claude_crew/subagents/general_purpose.md` frontmatter: `settingSources: ["user", "project"]` (was `[]`); add `skills: all`.
- DO NOT touch `claude_crew/subagents/explorer.md` or `claude_crew/subagents/planner.md` frontmatter (SC-10).
- Update `tests/test_user_loader.py:527` `test_bundled_packs_have_expected_setting_sources` (sentinel MF-3): `general-purpose` assertion flips from `[]` to `["user", "project"]`.
- Run `grep -rn "general-purpose" tests/` and audit each hit for stale frontmatter assumptions before declaring the task done (sentinel MF-3 audit).

**Acceptance Criteria**:

```
Scenario: bundled general-purpose declares skills: all
  Given the default subagent pack is loaded
  When parse_pack_file runs on general_purpose.md
  Then PackFrontmatter.skills == "all"
  And PackFrontmatter.settingSources == ["user", "project"]

Scenario: bundled explorer and planner unchanged
  Given the default pack is loaded
  Then PackFrontmatter for explorer has settingSources == [] and skills is None
  And PackFrontmatter for planner has settingSources == ["project"] and skills is None
```

**Tests** (`tests/test_user_loader.py`, `tests/test_pack_loader.py`):
- Update `test_bundled_packs_have_expected_setting_sources` (existing, MF-3)
- `test_bundled_general_purpose_has_skills_all` (SC-6)
- `test_bundled_explorer_planner_skills_unset` (SC-10 — defends #11's win)

**Verification**: `uv run pytest tests/test_user_loader.py tests/test_pack_loader.py` — all pass; `grep -rn "general-purpose" tests/ | grep -v "skills.*all\|settingSources.*user.*project"` — no stale assertions.

---

### Task 4: Cascade behavior + factory round-trip

**Depends on**: T1, T2, T3 | **Blocks**: T5

**Scope**:
- No production-code changes. This task is **tests only**, asserting that:
  - SC-7 (list-over-list cascade) works via `merge_packs` whole-key replacement
  - SC-8 (`"all"` ↔ list cascade) works (D-8)
  - D-2 cascade-removal (user `skills: []` removes default `skills: [a,b]`) works
  - D-9: `skills="all"` survives the SdkTeammate factory edge into `ClaudeAgentOptions`-bound kwargs

**Acceptance Criteria**:

```
Scenario: user override replaces default list
  Given default declares "skills: [a]" for role X
  And user pack declares "skills: [b]" for role X
  When build_merged_pack runs
  Then merged pack has role X with skills == ["b"]

Scenario: user override "all" replaces default list
  Given default declares "skills: [a]" for role X
  And user pack declares "skills: all" for role X
  When build_merged_pack runs
  Then merged pack has role X with skills == "all"

Scenario: user override list replaces default "all"
  Given default declares "skills: all" for role X
  And user pack declares "skills: [b]" for role X
  When build_merged_pack runs
  Then merged pack has role X with skills == ["b"]

Scenario: user override empty list removes default skills
  Given default declares "skills: [a, b]" for role X
  And user pack declares "skills: []" for role X
  When build_merged_pack runs
  Then merged pack has role X with skills is None

Scenario: skills "all" survives factory boundary into ClaudeAgentOptions kwargs
  Given the default pack is loaded (general-purpose has skills: all)
  When SdkTeammate is instantiated and the opts_kwargs assembly path is exercised
  Then opts_kwargs["skills"] == "all" (the string, not a list)
```

**Tests** (`tests/test_user_loader.py`, `tests/test_factories.py`, or new `tests/test_skills_cascade.py`):
- `test_skills_cascade_user_overrides_default` (SC-7)
- `test_skills_cascade_all_to_list_collapse` (SC-8 forward)
- `test_skills_cascade_list_to_all_collapse` (SC-8 reverse)
- `test_skills_cascade_user_empty_list_removes_default` (D-2 cascade-removal, sentinel L-1 — include `assert AgentDefinition().skills is None` setup-time probe so test doesn't pass vacuously if SDK changes default)
- `test_skills_all_passes_through_sdk_teammate_factory` (D-9 round-trip — assert at `opts_kwargs` boundary; mock `ClaudeSDKClient` to capture options)

**Verification**: `uv run pytest tests/test_user_loader.py tests/test_factories.py` — all pass.

---

### Task 5: End-to-end integration tests + docs + BACKLOG

**Depends on**: T1, T2, T3, T4 | **Blocks**: Phase 5 verification

This task is the cohesive E2E pipeline test plus the documentation that closes SC-9 plus the BACKLOG / vision linkage for #25.

**Happy Path Scenarios** (full pipeline via real loader cascade):

```
Scenario: User-defined agent with skills loaded end-to-end
  Given fixture skill dir at <home>/.claude/skills/test-skill/SKILL.md
  And fixture user agent at <home>/.claude/agents/test-role.md declaring skills: [test-skill]
  When default_factory() runs and a teammate is constructed for "test-role"
  Then no PackLoadError raised
  And no WARN log emitted for skill discovery
  And SdkTeammate's opts_kwargs["skills"] == ["test-skill"]
  And opts_kwargs["setting_sources"] is None (i.e., SDK will auto-inject)

Scenario: Bundled general-purpose teammate has skills: all end-to-end
  Given the default pack loaded (no user/project overrides)
  When SdkTeammate is constructed for "general-purpose"
  Then opts_kwargs["skills"] == "all"
  And opts_kwargs["setting_sources"] == ["user", "project"]

Scenario: Multi-agent cascade — user overrides default skills, then project overrides user
  Given default has general-purpose with skills: all
  And user pack has general-purpose with skills: [foo]
  And project pack has general-purpose with skills: [bar]
  When build_merged_pack runs
  Then merged has general-purpose with skills == ["bar"]
  And SdkTeammate.opts_kwargs["skills"] == ["bar"]
```

**Sad Path Scenarios**:

```
Scenario: User agent declares unknown skill — WARN at startup, teammate still spawns
  Given no skill dirs exist
  And user agent declares "skills: [missing]"
  When build_merged_pack runs
  Then a WARN log names "missing"
  And SdkTeammate spawns successfully with opts_kwargs["skills"] == ["missing"]
  And no exception is raised

Scenario: User agent declares skills + settingSources=[] — pack-load fails
  Given user agent declares "skills: [foo]" AND "settingSources: []"
  When discover_dir runs
  Then PackLoadError is raised AT THE INDIVIDUAL FILE (per existing strict_parse semantics — sibling files still load)
  And the merged pack does NOT contain that role
  And factories continue to function for other roles

Scenario: Project skill dir exists but cwd differs (A-7 cwd trap)
  Given <project>/.claude/skills/proj-skill/SKILL.md exists
  And cwd at server start is set to a non-project dir
  And a role declares skills: [proj-skill]
  When build_merged_pack runs
  Then a WARN is emitted (operator's misconfigured launch, expected behavior)
```

**Live-probe checklist (SC-1b — manual gate at Phase 5)**:
- [ ] If running CLAUDE_CREW_LIVE_TESTS=1, the live SC-1b probe MUST plant a marker via the skill body (not via the question prompt). Write a test skill that runs Bash to write a unique UUID to `/tmp/<uuid>.marker` and assert the file exists post-run, not that the agent text contains the UUID.
- [ ] No assertion on token counts or cost.
- [ ] Tool-name correctness verified by file existence, not narration.

**Tests** (`tests/test_skills_e2e.py` — new file):
- `test_e2e_user_agent_skills_loaded` (happy path 1)
- `test_e2e_bundled_general_purpose_skills_all` (happy path 2)
- `test_e2e_three_layer_cascade_project_wins` (happy path 3)
- `test_e2e_unknown_skill_warns_but_loads` (sad path 1)
- `test_e2e_skills_settingsources_conflict_isolates_to_one_file` (sad path 2 — defends sibling files)
- `test_e2e_cwd_trap_produces_warn` (sad path 3 — A-7)

**Documentation** (closes SC-9):
- Update `README.md` (or `CLAUDE.md` per project convention) with:
  (a) An example user agent declaring `skills: [foo]` + the matching `~/.claude/skills/foo/SKILL.md` fixture
  (b) The settingSources interaction error and how to fix it
  (c) The cwd-trap note (A-7) — launch claude-crew from project root
  (d) The literal stderr line format the WARN produces (sentinel M-4):
  ```
  WARNING claude_crew.subagents.loader: agent 'reviewer' declares unknown skills ['security-review'] — not found in user or project skill dirs at startup; teammate will fail to invoke them at runtime
  ```
  (e) A note that startup WARNs do NOT reach the dashboard today; tracked as vision row #25.

**BACKLOG / vision linkage**:
- File a BACKLOG entry referencing #25 ("Startup diagnostics surfaced on the dashboard — promoted from idea to next once #23 ships").

**Verification**: `uv run pytest tests/test_skills_e2e.py` — all pass; full suite `uv run pytest` — no regressions; manual review of the README/CLAUDE.md doc additions; BACKLOG entry exists.

---

**Phase 3 Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E task (T5) with happy + sad path coverage
- ✅ Each Phase 2 SC and D traces to at least one test in carried-into pointers
- ✅ Verification commands fail without the feature
- ✅ Pre-existing-test drift items (sentinel MF-3) explicitly named in T3
- ✅ User approval to proceed to Phase 4

---

## Phase 4: Implementation

*Execution driven by SKILL.md.*

---

## Phase 5: Completion

*To be filled at end.*
