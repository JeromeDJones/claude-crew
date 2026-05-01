# Feature: Broker Snapshot API + Dashboard Polish (#18)

**Status**: ✅ Shipped (merged to master 2026-04-30)
**Created**: 2026-04-30
**Merged**: 2026-04-30 (commit `2a93977`)

---

## Phase 1: Research & Requirements

### Problem Statement

`UIServer` reaches into private `Broker` attributes at 4 production sites: `broker._info` (lines 112, 176), `broker._teammates` (line 114), `broker._log` (line 158). This is fragile coupling — `Broker` can't evolve its internal state without risking silent UIServer breakage; UIServer can't be unit-tested without standing up a live `Broker` with real teammate spawns. The recon for #18 (Haiku explorer, 2026-04-30) confirmed the production surface is small: 4 reads, all in `ui_server.py`. Test code reads private attrs in many places, but tests reading private API is acceptable — the design gap is production code doing it.

The current arrangement also blocks #19 (tool-use events in dashboard stream): #19 needs another broker-level read surface, and adding it as a fifth `_attr` access compounds the coupling. Doing #18 first means #19 ships against a clean public read API.

Two adjacent low-cost cleanups bundle into the same feature naturally:

1. **Hardcoded "main" git branch** in `_unreachable_instance` (`ui_server.py:67`) and `_build_local_instance` (`ui_server.py:184`). Operators viewing a multi-instance dashboard can't tell which checkout each crew is running on — every row says "main" regardless. Real value: read `git branch --show-current` once at UIServer init, cache it, fall back to `"main"` on any error.

2. **Stale `_get_redaction_version()` ImportError fallback** (`teammate.py:50-62`). The function tries `from claude_crew.redaction import REDACTION_VERSION` and falls back to literal `"v1"` on `ImportError`. The TODO comment says "remove the fallback once T1 is merged and redaction.py is stable" — that T1 (F8 tool-execution telemetry) shipped weeks ago. The fallback is dead code; the import always succeeds.

Bundled because (a) all three are XS individually, (b) they're all consumed by `ui_server.py` or the modules it touches, (c) shipping them together means one feature lifecycle for three small wins instead of three lightweight features clogging the pipeline.

### Success Criteria

- [ ] **SC-1**: `Broker.snapshot()` is a public method returning a frozen, plain-data structure (a `@dataclass(frozen=True)` or similar). The structure contains every piece of broker state that the production read sites need: `crew_id`, the full set of `TeammateInfo` records (alive + tombstoned, in stable order), per-alive-teammate live status data (output of `Teammate.status_snapshot()` captured eagerly), and the message log (per OQ-2 resolution: param-controlled slice). No method references; no live mutable references *to broker-owned mutable containers* — see SC-3 for the per-field freezing/deep-copy contract. Scope clarification: `Envelope.payload` is typed `Any` (often a dict); payload-content immutability is by convention, not enforced — see SC-13's note on the log freezing scope.

- [ ] **SC-2**: `claude_crew/ui_server.py` reads zero internal state from any broker- or teammate-shaped object in production paths. Tightened from the earlier draft after Phase 2 surfaced the hidden `teammate._model` read: the rule is "no underscore-prefixed attr reads in production paths" on either the broker or any teammate. Verified by `grep -E '(broker|teammate)\._\w+' claude_crew/ui_server.py` returning no matches AND `grep -E 'broker\.crew_id' claude_crew/ui_server.py` returning no matches in production paths. Constructor / DI sites are exempt (UIServer still receives a `Broker` reference at init for `snapshot()` calls — that's `self._broker`, not `broker._x`). Tests reading private API for setup remain acceptable.

- [ ] **SC-3**: Snapshot consistency is enforced structurally, not by convention.
  (a) **Atomicity by sync execution**: `snapshot()` is a synchronous (non-`async`, no `await`) method. Single-threaded asyncio + sync execution = the broker cannot mutate between two field assignments inside the snapshot constructor.
  (b) **Deep-frozen status data**: `Teammate.status_snapshot()` returns `dict[str, Any]` today and may share live internal references. The snapshot must produce a *value-copied* status dict (e.g., `dict(status)`) at build time so callers cannot reach back into teammate internals via the snapshot. Where `status_snapshot()` returns nested mutable values (lists of tool entries, dicts of subagent state), those are similarly copied (one level of value copy is sufficient for current shapes; document if deeper).
  (c) **Frozen wrapper types**: `TeammateInfo` is already `@dataclass(frozen=True)`; `Envelope` is already frozen. The snapshot type itself is `@dataclass(frozen=True)`. Tuples used for collections (not lists). Test `test_snapshot_isolated_from_teammate_state_mutation` mutates a teammate's internal status dict AFTER snapshot is built and asserts the snapshot's view is unchanged.

- [ ] **SC-4**: Eager status capture — live teammate status is read at `snapshot()` build time, not lazily on later access. Implication: the cost of `snapshot()` scales linearly with the alive teammate count. Acceptable: `_build_local_instance` already calls `status_snapshot()` per alive teammate today; `snapshot()` is a refactor, not an additional cost. Verified by the same test as SC-3(c) — mutating after the call returns must not affect the snapshot's view.

- [ ] **SC-5**: `Broker.snapshot()` is performant enough for the UIServer poll path (every `_POLL_INTERVAL` seconds — currently 1.5s). Specifically: snapshot construction is in-memory only (no I/O, no subprocess, no file reads), and the per-call cost is at most O(N teammates + M log entries) where N and M are the existing data structure sizes. No regression in `_build_state()` wall-clock vs. pre-feature.

- [ ] **SC-6**: The hardcoded `"branch": "main"` string in `_build_local_instance` (`ui_server.py:184`) is replaced with the result of `git branch --show-current` invoked via `subprocess.run` with `cwd=<UIServer-determined cwd>`. Source of cwd: `os.getcwd()` at `UIServer.__init__` time (default), with an optional `cwd` constructor param for tests. The detected branch is cached as a `UIServer` instance attribute. Refresh policy is governed by OQ-5. The corresponding string in `_unreachable_instance` (line 67) stays `"main"` per SC-12.

- [ ] **SC-7**: Git branch detection failures (not a git repo, `git` command not on PATH, subprocess error, non-zero exit, empty output, timeout >2s) cause the cached branch to fall back to the literal string `"main"` without raising an exception or printing a stack trace. The failure path is logged at DEBUG level only — operators with detached-HEAD or non-git checkouts shouldn't see noise.

- [ ] **SC-8**: `_get_redaction_version()` in `claude_crew/teammate.py` is removed entirely (function definition + both call sites at lines 154 and 205). Replaced with a direct `from claude_crew.redaction import REDACTION_VERSION` at module-level, used inline. Equivalent behavior: every snapshot still carries `redaction_version: "v1"` for live teammates.

- [ ] **SC-9**: No regression — full test suite passes at the same green count as current `master` (511 passed, 10 skipped) plus any new tests added for #18. Specifically: every test that currently uses `broker._info`, `broker._teammates`, `broker._log` in test setup continues to work unchanged. The snapshot is additive; private attrs are not removed.

- [ ] **SC-10**: UIServer's state-building code path (`_build_state` and helpers) accepts a `BrokerSnapshot` directly and produces the dashboard payload without dereferencing `self._broker` for state. New test `test_build_state_from_synthetic_snapshot` constructs a `BrokerSnapshot` instance with hand-rolled data (no `Broker` involved) and a UIServer instance whose `_broker` is `None` (or a stub); asserts `_build_state()` returns the expected dict. UIServer's constructor still takes a `Broker` reference for live operation (calling `snapshot()` on the poll loop), but the state-build path itself is broker-decoupled at function-input level.

- [ ] **SC-11**: The new `BrokerSnapshot` (or equivalent) type is importable cleanly from `claude_crew.broker` (per OQ-4 resolution). Type annotations on `UIServer._build_state` reference it explicitly. Downstream consumers (and #19 when it lands) can write to a stable contract.

- [ ] **SC-12**: Multi-instance compatibility preserved. Reachable peer instances' branches are real values — each peer's UIServer reads its own branch (SC-6) and includes it in its own `_build_local_instance` output, which the leader's HTTP fanout (#13) merges into the unified dashboard. Only `_unreachable_instance` (the placeholder for peers whose HTTP fetch failed) keeps `branch: "main"` as the unknown-state placeholder. The local-vs-remote distinction stays intact for #13's multi-instance dashboard.

- [ ] **SC-13**: Forward-compat field reservation for #19. `BrokerSnapshot` includes a `tool_events: tuple[Any, ...] = ()` field (or equivalent — exact type TBD by #19's needs), defaulting to empty tuple. #18 populates it as empty; #19 fills it. Adding the field with a default value preserves additive-compatibility for any future #19 work without forcing a snapshot v2.

### Questions

- [x] **OQ-1 RESOLVED** (co-architect, 2026-04-30): **Embedded `LiveTeammateInfo(info, status)`.** The snapshot exposes two collections — `teammates: tuple[TeammateInfo, ...]` (all, ordered) and `live: tuple[LiveTeammateInfo, ...]` (alive subset with status). Dead teammates carry no status field. Cohesion wins: SC-3 atomicity becomes a type-system guarantee rather than a runtime invariant the producer must remember. A separate dict makes half-built snapshots possible; embedded makes them impossible.

- [x] **OQ-2 RESOLVED** (co-architect, 2026-04-30): **Snapshot-slices, with `log_limit: int | None = None` parameter.** Default `None` returns the full log (already bounded by the broker's deque); UIServer passes `200`. Caller controls the cap, broker honors it cheaply when asked. Avoids materializing thousands of envelopes for the caller to discard.

- [x] **OQ-3 RESOLVED** (co-architect, 2026-04-30): **UIServer owns branch detection.** Keeping `subprocess`/git out of `Broker` preserves SC-5's "in-memory only" guarantee and keeps `snapshot()` testable without a git repo. Broker doesn't have cwd as a first-class attribute today, and inventing one for git's benefit is the wrong direction. Multi-instance peers do their *own* local read.

- [x] **OQ-4 RESOLVED** (co-architect, 2026-04-30): **`BrokerSnapshot` and `LiveTeammateInfo` live in `broker.py`.** Colocate producer with output type. Premature to extract until a second consumer needs it.

- [ ] **OQ-5**: Branch refresh policy. SC-6 says "captured at `__init__`," SC-7 caps subprocess at 2s. At a 1.5s `_POLL_INTERVAL`, refresh-per-snapshot is too expensive. Three options:
  (a) **Init-only** — never refresh; user must restart claude-crew after `git checkout`. Simplest, but the dashboard goes silently stale across branch switches.
  (b) **TTL-cached** — refresh every N seconds (co-architect recommended 30s). Cost: one subprocess per 30s per UIServer. Catches branch switches mid-session within 30s.
  (c) **On-demand** — refresh whenever the cached value is older than some N, but only if `snapshot()` is called (don't run a background timer). Equivalent to (b) in steady-state.
  **Recommend (b) with 30s TTL** unless objections. Phase 2 must pin the value.

- [ ] **OQ-6** (recorded but pre-resolved by SC-2 and SC-10): UIServer must source `crew_id` from the snapshot, not `self._broker.crew_id`. SC-2 and SC-10 already enforce this — listed here so Phase 2 acknowledges the change explicitly.

### Constraints & Dependencies

- **Requires**: `ui_server.py:_build_state` and `_build_local_instance` (the consumers being refactored), `broker.py` internal state (the producer), `teammate.py:_get_redaction_version` (the third bundle item — independent of the snapshot work).
- **Breaking changes**: None at the public API level. `Broker.snapshot()` is additive; private `_info`/`_log`/`_teammates` stay where they are. Tests that read private attrs continue to work.
- **Forward compatibility**: Snapshot design should anticipate #19's needs. #19 surfaces tool-use events on the dashboard; the broker-level data #19 needs (per-teammate event list or a parallel channel) should fit cleanly into the snapshot shape, not require a snapshot v2.
- **Multi-instance compatibility**: #18 must not break #13's multi-instance dashboard aggregation. UIServer's `_unreachable_instance` (the placeholder used for peers we couldn't reach) hardcodes the same `"branch": "main"` string today — that one stays "main" because we can't determine the remote instance's branch from outside; only the local instance gets the real branch read (SC-12).
- **No new dependencies**: stdlib only — `subprocess` for git, `dataclasses` for the snapshot type. Both already in use.
- **Performance**: `snapshot()` runs every `_POLL_INTERVAL` (1.5s) per alive UIServer instance. Must remain in-memory only. Multi-instance fanout already has this constraint and works fine; this preserves it.
- **Sequencing**: This feature gates #19. Recommended order: ship #18, then re-scope #19 against the snapshot read surface.

**Gate**: Questions captured for Phase 2 routing, success criteria measurable, constraints documented, user confirmed.

---

## Phase 2: Design & Specification

### Architecture Overview

```
       Broker internal state                        UIServer state-build path
       ──────────────────────                       ──────────────────────────
       _info: dict[id, TeammateInfo]   ┐
       _teammates: dict[id, Teammate]  │            _build_state()
       _log: list[Envelope]            ├──┐           │
       crew_id: str                    │  │           ▼
                                       ▼  │     snapshot = broker.snapshot(log_limit=200)
                              snapshot()  │           │
                                       │  │           ▼
                                       ▼  │     _build_local_instance(snapshot)
                                BrokerSnapshot │      │  reads snapshot.live[],
                                  ├ crew_id    │      │        snapshot.teammates[],
                                  ├ teammates  │      │        snapshot.log,
                                  ├ live       │      │        snapshot.crew_id
                                  ├ log        │      │  reads self._get_branch() for
                                  └ tool_events│      │        the local instance row
                                  (#19 reserve)│      ▼
                                              │   instance + transcript dicts
                                              ▼
                                        UIServer init: detect cwd-of-process,
                                        compute git branch with 30s TTL refresh
```

The data flow becomes one-direction: `Broker.snapshot()` produces a frozen value, UIServer consumes it. Production code paths in `ui_server.py` no longer reach into broker internals (`_info`, `_teammates`, `_log`, `_model`) or even the public `broker.crew_id` — every state field comes from the snapshot.

Hidden read site discovered during Phase 2 synthesis (not in the original recon): `_build_local_instance` reads `teammate._model` at `ui_server.py:122` to populate the agent's `model` field. This is a *fifth* private-attr read in production. The snapshot's `LiveTeammateInfo.model` field absorbs it.

### Data / API Contracts

```python
# claude_crew/broker.py — new types, colocated with producer (OQ-4 resolution)

@dataclass(frozen=True)
class LiveTeammateInfo:
    """Pairs a TeammateInfo with the alive-teammate-only fields the UI needs."""
    info: TeammateInfo
    status: dict[str, Any]   # value-copied at snapshot build (D-2); safe post-snapshot
    model: str | None        # from teammate._model; None for StubTeammate / unset

@dataclass(frozen=True)
class BrokerSnapshot:
    """Frozen, value-copied view of broker state for downstream consumers (UIServer, future)."""
    crew_id: str
    teammates: tuple[TeammateInfo, ...]   # all (alive + dead), insertion order from _info
    live: tuple[LiveTeammateInfo, ...]    # subset corresponding to alive teammates only
    log: tuple[Envelope, ...]              # last log_limit envelopes (or all if log_limit is None)
    tool_events: tuple[Any, ...] = ()      # #19 reservation; empty in #18 (D-10 / SC-13)

class Broker:
    def snapshot(self, log_limit: int | None = None) -> BrokerSnapshot:
        """Return a frozen, value-copied snapshot of broker state.

        Synchronous — single-threaded asyncio + no awaits guarantees atomicity (D-1).
        log_limit=None returns the full log; an int returns the last N entries.
        """
        # 1. Iterate _info.values() once, capturing TeammateInfo references in tuple
        # 2. For each alive entry, look up teammate from _teammates, call status_snapshot(),
        #    wrap with try/except (preserve current behavior); value-copy the dict
        # 3. snapshot teammate._model via getattr(teammate, "_model", None)
        # 4. Slice _log per log_limit (full or last-N)
        # 5. Construct BrokerSnapshot with crew_id

# claude_crew/ui_server.py

class UIServer:
    _BRANCH_TTL_SECONDS = 30  # D-6 cache lifetime
    _BRANCH_DETECT_TIMEOUT = 2.0  # SC-7 subprocess cap

    def __init__(
        self,
        broker: Broker,
        port: int = 7821,
        registry: InstanceRegistry | None = None,
        sock: "Any | None" = None,
        cwd: str | None = None,  # NEW: optional override for tests (D-7)
    ) -> None:
        ...
        self._cwd = cwd or os.getcwd()
        self._branch_cache: tuple[str, float] = ("main", 0.0)  # (value, expires_at)

    def _get_branch(self) -> str:
        """Cached git branch; refreshes every _BRANCH_TTL_SECONDS. Falls back to 'main' on error."""
        now = time.time()
        if now < self._branch_cache[1]:
            return self._branch_cache[0]
        branch = _detect_branch(self._cwd) or "main"
        self._branch_cache = (branch, now + self._BRANCH_TTL_SECONDS)
        return branch

    def _build_local_instance(
        self, snapshot: BrokerSnapshot
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Build the local broker's instance dict and transcript list FROM A SNAPSHOT.
        Production-path SC-2: reads zero broker attrs (private or public).
        """

    async def _build_state(self, local_only: bool = False) -> dict[str, Any]:
        snapshot = self._broker.snapshot(log_limit=200)
        local_instance, local_messages = self._build_local_instance(snapshot)
        transcripts = {snapshot.crew_id: local_messages}
        ...

# Module-level helper (private) in ui_server.py
def _detect_branch(cwd: str) -> str | None:
    """Run `git -C cwd branch --show-current`. Returns branch name or None on any failure."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True, text=True, timeout=UIServer._BRANCH_DETECT_TIMEOUT,
        )
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        return branch or None  # empty string = detached HEAD → fail to None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
```

`teammate.py` — D-9:

```python
# Module level (replaces the function at lines 50-62)
from claude_crew.redaction import REDACTION_VERSION

# Both prior call sites (lines 154, 205): replace _get_redaction_version() with REDACTION_VERSION
```

### Design Decisions

- **D-1: Snapshot is synchronous AND in-memory only.** — *Rationale:* SC-3(a) + SC-5. `Broker.snapshot()` is `def`, not `async def`. No `await` inside. No subprocess, no file I/O, no network. Single-threaded asyncio + sync execution + no I/O = no torn reads, no event-loop yields, no surprise blocking. — *Carried into:* `Broker.snapshot()` signature; test `test_snapshot_is_synchronous_method` asserts `inspect.iscoroutinefunction(Broker.snapshot) is False`. The "no I/O" half is enforced by code review (no test).

- **D-2: Status dicts are deep-copied at build time.** — *Rationale:* SC-3(b). `Teammate.status_snapshot()` builds a fresh outer dict and fresh `current_tools` / `current_subagents` lists, BUT inner fields `last_tool_completed` and `last_subagent_completed` are typed `dict[str, Any] | None` and reference the live `teammate._last_tool_completed` / `_last_subagent_completed` mutable dicts (verified — the snapshot dict literally contains these references, not copies of them). One-level `dict(status)` does NOT copy these inner dicts. **Resolution: use `copy.deepcopy(status)` at snapshot build time.** Cost is negligible given the shape (a few dicts and short lists per teammate). — *Carried into:* implementation of `snapshot()` in broker.py uses `copy.deepcopy`; test `test_snapshot_isolated_from_teammate_state_mutation` mutates `teammate._last_tool_completed["new_key"] = "new_value"` post-snapshot AND mutates the outer status dict, asserting the snapshot is unchanged in both cases.

- **D-3: Model is captured from `teammate._model` via `getattr(teammate, "_model", None)`.** — *Rationale:* StubTeammate has no `_model`; SdkTeammate stores it as a private attribute. We *could* promote `model` to a public attribute on Teammate as part of #18, but that's scope creep — defer to a future feature when more attributes need promoting. — *Carried into:* snapshot build code reaches into the private; documented in `LiveTeammateInfo.model` docstring.

- **D-4: `LiveTeammateInfo` embeds `TeammateInfo` (does not duplicate fields).** — *Rationale:* OQ-1 resolution. Single source of truth for teammate identity/lifecycle. `live` tuple references the same `TeammateInfo` objects as `teammates` tuple — frozen dataclasses are safe to share. — *Carried into:* type definition.

- **D-5: Log slicing happens at snapshot time, controlled by `log_limit` param.** — *Rationale:* OQ-2 resolution. Default `None` returns full log (already bounded by broker's history); UIServer passes `log_limit=200`. — *Carried into:* `Broker.snapshot(log_limit)` signature.

- **D-6: Branch detection uses a 30-second TTL cache, refreshed in-line.** — *Rationale:* OQ-5 option (b). Refresh-per-snapshot (every 1.5s) is too costly; init-only goes silently stale on `git checkout`. 30s is a fair tradeoff. Refresh is synchronous in `_get_branch()` — co-architect noted ~5ms blip every 30s, acceptable. — *Carried into:* `UIServer._get_branch` implementation; test `test_branch_cache_ttl_honors_30s_window`.

- **D-7: cwd source: `UIServer.__init__(cwd=...)` constructor param, defaults to `os.getcwd()` at init time.** — *Rationale:* SC-6 needed pinning. Tests inject explicit cwd; production uses process cwd. Broker stays cwd-free (per OQ-3 resolution). — *Carried into:* `UIServer.__init__` signature; test passes a temp dir.

- **D-8: `_unreachable_instance` keeps `branch="main"`.** — *Rationale:* SC-12. Adding remote-branch info would require an additional wire field on the multi-instance fanout; out of scope for #18. The unknown-state placeholder is the existing semantics. — *Carried into:* `_unreachable_instance` body unchanged.

- **D-9: `_get_redaction_version()` removed; replaced with module-level import.** — *Rationale:* SC-8. The ImportError fallback is dead code (T1 of F8 shipped weeks ago; `redaction.py` is stable). — *Carried into:* teammate.py module-level `from claude_crew.redaction import REDACTION_VERSION`; both call sites use the bare name.

- **D-10: `BrokerSnapshot.tool_events: tuple[Any, ...] = ()` field reserved for #19.** — *Rationale:* SC-13. Additive default; #19 populates it. **Contract for #19 implementers:** events form a flat tuple with each carrying its own `teammate_id` field for downstream dispatch. The shape is `tuple[ToolEvent, ...]`, NOT `dict[teammate_id, tuple[ToolEvent, ...]]`. If #19 needs per-teammate keying, that is a different field (e.g., `tool_events_by_teammate`) added separately rather than reshaping this field. — *Carried into:* dataclass field; test verifies field exists with empty default; D-10 note pinned for #19 design.

- **D-11: UIServer constructor still requires a `Broker` reference (for live `snapshot()` calls), but `_build_state` and `_build_local_instance` are broker-decoupled at function-input level.** — *Rationale:* SC-10. Strict "no broker at all" would mean the WebSocket loop has nothing to call snapshot on. Realistic decoupling: state-build path (the testable surface) takes a `BrokerSnapshot` parameter. — *Carried into:* `_build_local_instance(snapshot)` signature; test `test_build_state_from_synthetic_snapshot` constructs UIServer with no real broker (or stub) and calls `_build_local_instance(synthetic_snapshot)` directly.

### Edge Cases

1. **Empty crew (no spawns)** — `snapshot.teammates == ()`, `snapshot.live == ()`, `snapshot.log == ()`. `_build_local_instance` produces an "idle" instance with empty agents.
2. **All teammates tombstoned** — `snapshot.live == ()`, `snapshot.teammates` non-empty. F14 aggregate logic still sums `_at_death` values from `snapshot.teammates` for dead entries.
3. **`status_snapshot()` raises** — current code catches and uses `snap = {}`. Snapshot build replicates: try/except, store `status={}` in the LiveTeammateInfo.
4. **`teammate._model` not set** — `getattr(default=None)`. `LiveTeammateInfo.model` is None; `_normalize_model(None)` returns `"sonnet"` (existing behavior preserved).
5. **Log smaller than `log_limit`** — slice returns what exists; no padding.
6. **Concurrent teammate death between `_info.values()` iteration and per-teammate lookup** — sync execution prevents this mid-snapshot. Snapshot N may capture a teammate alive while snapshot N+1 sees tombstone — expected.
7. **First `_get_branch()` call** — pays subprocess cost on the hot path (~5-50ms one-time). Subsequent calls within 30s use cache.
8. **UIServer in non-git directory** — `_detect_branch` returns None → cache stores "main", expires every 30s, refreshes still fail. No backoff in v1; consistent behavior is fine.
9. **Detached HEAD** — `git branch --show-current` returns empty. `_detect_branch` treats empty as None → "main".
10. **`git` not on PATH** — subprocess raises `FileNotFoundError`; caught; → "main".
11. **`git` subprocess hangs** — `timeout=2.0` raises `subprocess.TimeoutExpired`; caught; → "main".
12. **Branch contains unusual characters (e.g., `feat/foo bar`)** — pass through verbatim; dashboard renders as-is. Not our problem to sanitize.
13. **Test path** — `_build_state` must not error if `self._broker` is `None`. SC-10 specifies: `_build_state` calls `self._broker.snapshot(...)`, so this would raise. Resolution: tests don't call `_build_state()`; they call `_build_local_instance(synthetic_snapshot)` directly. Documented in the SC-10 test pattern.

### Specification

Implementation order (Phase 3 will refine into tasks):
1. Add `LiveTeammateInfo` and `BrokerSnapshot` dataclasses + `Broker.snapshot()` method to `broker.py`.
2. Refactor `UIServer._build_local_instance` to accept a `BrokerSnapshot` parameter; refactor `_build_state` to call `self._broker.snapshot(log_limit=200)` and pass it down.
3. Add `_detect_branch()` module-level helper + `_get_branch()` instance method + `_BRANCH_TTL_SECONDS` to UIServer; thread `cwd` constructor param.
4. Replace hardcoded `"branch": "main"` at `ui_server.py:184` with `self._get_branch()`. Leave `_unreachable_instance` unchanged.
5. Remove `_get_redaction_version()` from `teammate.py`; module-level import; update both call sites.
6. Tests:
   - `test_snapshot_is_synchronous_method` (D-1)
   - `test_snapshot_isolated_from_teammate_state_mutation` (D-2 / SC-3 / SC-4)
   - `test_snapshot_log_limit_param` (D-5)
   - `test_snapshot_tool_events_default_empty` (D-10)
   - `test_build_state_from_synthetic_snapshot` (SC-10)
   - `test_ui_server_no_broker_private_attr_reads_in_production` (SC-2 grep-style assertion or AST-based)
   - `test_branch_detection_falls_back_to_main_on_error` (SC-7)
   - `test_branch_cache_ttl_honors_30s_window` (D-6)
   - `test_redaction_version_is_inlined` (SC-8 — assert `_get_redaction_version` is gone)

### Assumptions

- **A-1**: `teammate._model` is the only place model id is stored on a Teammate instance. — *Rationale:* `_model` appears uniquely in SdkTeammate; StubTeammate has no model. Verified by grep.
- **A-2**: `Teammate.status_snapshot()`'s overall shape is shallow enough that `copy.deepcopy(status)` is cheap (a few dicts and short lists per teammate; deep-copy is microseconds). One-level value-copy was tried first and rejected because `last_tool_completed` / `last_subagent_completed` are typed `dict[str, Any] | None` referencing live mutable teammate state. Deep-copy is the simpler, more robust choice given the small data and the snapshot's atomicity contract. — *Rationale:* Phase 2 review surfaced the inner-dict leak; D-2 resolution is `copy.deepcopy`.
- **A-3**: `_unreachable_instance` is the only render path for non-local instances' branch field. — *Rationale:* verified — line 67 is the sole site.
- **A-4**: A 30-second TTL on git branch is acceptable to operators (worst case: dashboard shows old branch for up to 30s after `git checkout`).
- **A-5**: `git branch --show-current` is available (git >= 2.22, released 2019). Modern enough for any plausible dev environment in this codebase.
- **A-6**: Tests for the snapshot can use `dataclasses.replace` and direct `BrokerSnapshot(...)` construction — no factory needed. Same pattern as TeammateInfo elsewhere.
- **A-7**: `_get_redaction_version`'s ImportError fallback truly is dead — `claude_crew.redaction` is the canonical source and has been stable since F8 T1. — *Rationale:* TODO comment in the function says so; verified by inspection of `redaction.py`.

### Open Questions

- [ ] **OQ-7** (Phase 3 deferrable, recommend deferred): Should `branch` move into `BrokerSnapshot` (so peer instances also report their branch via the snapshot, not via UIServer-local state)? **Recommendation: no for #18.** Keeps Broker free of git/subprocess concerns (per OQ-3). If multi-instance branch display becomes a real ask post-#18, that's a separate small feature. Recording for the record so Phase 3 doesn't relitigate.

- [ ] **OQ-8** (Phase 3 deferrable, recommend deferred): Should the `_model` access be cleaned up by promoting `Teammate.model` to a public attribute as part of #18? **Recommendation: no.** Scope creep — D-3 documents the workaround, and a future "public Teammate attrs" pass can clean up `_model`, `_subagent_uses`, etc. together. Recording for the record.

**Gate**:
- ✅ Design clear and justifiable
- ✅ Spec comprehensive — no ambiguity, no TODOs (OQ-7 / OQ-8 explicit deferrals)
- ✅ Edge cases listed (13 items)
- ✅ Error handling specified (subprocess failure modes, status_snapshot raise, missing _model, log slice underflow)
- ✅ Cross-feature integration check complete (#13 multi-instance via SC-12; #19 forward-compat via SC-13/D-10; F14 cost aggregation preserved via edge case 2)
- ✅ Implementable by someone with no additional context

---

## Phase 3: Task Breakdown

Five tasks. T1+T2 form the architectural core (sentinel review gates between T2 and T3). T3 and T4 are independent of T1/T2 and could parallelize, but the linear chain is recommended to keep sentinel context tight. T5 is the integration cap.

**Branch**: `feature/broker-snapshot-dashboard-polish` (created at start of Phase 4).

### Task T1 — `BrokerSnapshot` types + `Broker.snapshot()` method

**Goal**: define `LiveTeammateInfo` and `BrokerSnapshot` frozen dataclasses in `broker.py`. Implement `Broker.snapshot(log_limit: int | None = None) -> BrokerSnapshot`. Method is sync (no `await`), in-memory only (no I/O), uses `copy.deepcopy` for status dicts (D-2). Reserves `tool_events` field for #19 (D-10).

**Anchors**: D-1, D-2, D-3, D-4, D-5, D-10, SC-1, SC-3, SC-4, SC-5, SC-11, SC-13.

**BDD**:
```
Scenario: snapshot exposes all live teammates with embedded info+status+model
  Given a broker with two alive teammates A (model=opus) and B (model=sonnet)
  When I call broker.snapshot()
  Then snap.live has length 2
  And snap.live[i].info is the corresponding TeammateInfo
  And snap.live[i].status equals teammate.status_snapshot() at call time
  And snap.live[i].model is the teammate's _model attribute

Scenario: snapshot is isolated from teammate state mutation (deep-copy)
  Given a broker with one alive teammate
  And teammate.status_snapshot() returns a dict where last_tool_completed has key "name"
  When I call snap = broker.snapshot()
  And then teammate._last_tool_completed["new_key"] = "leak"  # mutate inner dict
  And teammate's outer status dict is also mutated
  Then snap.live[0].status["last_tool_completed"] does NOT contain "new_key"
  And the outer mutation is also not reflected

Scenario: snapshot is synchronous
  When inspect.iscoroutinefunction(Broker.snapshot)
  Then it returns False

Scenario: log_limit param honors the cap
  Given a broker with 500 envelopes in _log
  When snap = broker.snapshot(log_limit=200)
  Then len(snap.log) == 200
  When snap = broker.snapshot(log_limit=None)
  Then len(snap.log) == 500

Scenario: snapshot includes both alive and tombstoned teammates in `teammates`
  Given a broker with one alive A and one tombstoned B
  When I call broker.snapshot()
  Then snap.teammates contains both A and B (length 2, ordered by spawn)
  And snap.live contains only A (length 1)

Scenario: tool_events is empty by default
  Given any broker
  When I call broker.snapshot()
  Then snap.tool_events == ()
```

**Tests**: `test_snapshot_includes_alive_teammates_with_info_status_model`, `test_snapshot_isolated_from_teammate_state_mutation`, `test_snapshot_is_synchronous_method`, `test_snapshot_log_limit_param`, `test_snapshot_teammates_includes_alive_and_dead`, `test_snapshot_tool_events_default_empty`.

**Verification**: `uv run pytest tests/test_broker.py -v -k "snapshot"`. Fails if any of the 6 scenarios isn't met.

**Dependencies**: none.

---

### Task T2 — UIServer consumes the snapshot (no broker private reads)

**Goal**: refactor `UIServer._build_local_instance` to accept a `BrokerSnapshot` parameter and read all state from it. `_build_state` calls `self._broker.snapshot(log_limit=200)` and passes it down. Eliminates the 5 production read sites (`broker._info`, `broker._teammates`, `broker._log`, `broker.crew_id`, `teammate._model`). Surfaces SC-10 by making `_build_local_instance` testable without a live broker.

**Anchors**: D-11, SC-2, SC-10.

**BDD**:
```
Scenario: _build_local_instance accepts a synthetic snapshot
  Given a hand-rolled BrokerSnapshot with 1 LiveTeammateInfo (cost=$0.25, tokens 100/50)
    and 1 dead TeammateInfo (cost_at_death=$0.10)
    and 5 envelopes in log
  When ui = UIServer(broker=None, ...)  # or stub
    and instance, messages = ui._build_local_instance(snapshot)
  Then instance["agents"] has length 1 (alive only)
    and instance["agents"][0]["cost"] == 0.25
    and instance["cost"] == 0.35  (alive + dead, F14 aggregation preserved)
    and len(messages) <= 5

Scenario: ui_server.py contains zero private-attr reads on broker or teammate (production paths)
  Given the post-T2 ui_server.py
  When grep -E '(broker|teammate)\._\w+' claude_crew/ui_server.py
  Then no production-path matches
  And grep -E 'broker\.crew_id' claude_crew/ui_server.py returns no production-path matches

Scenario: dashboard payload shape is unchanged (no regression)
  Given a live broker with 2 teammates after a few turns
  When _build_state() is called pre-T2 and post-T2 with equivalent state
  Then the JSON-serialized dashboard payload is structurally identical
    (same keys, same value types) — verified by deep equality on a snapshot
```

**Tests**: `test_build_state_from_synthetic_snapshot`, `test_ui_server_no_broker_private_attr_reads_in_production`, `test_dashboard_payload_shape_unchanged_post_refactor` (compare a known input through both paths or via golden file).

**Verification**: `uv run pytest tests/test_ui_server.py -v` plus the grep test (which can be a `subprocess.run` inside a test).

**Dependencies**: T1.

---

### Task T3 — Branch detection in UIServer (cached, TTL-refresh)

**Goal**: implement `_detect_branch(cwd: str) -> str | None` module-level helper using `subprocess.run(['git', '-C', cwd, 'branch', '--show-current'], timeout=2.0, ...)`. Add `UIServer._get_branch()` instance method with 30s TTL cache. Thread `cwd` constructor param (default `os.getcwd()`). Replace the hardcoded `"branch": "main"` at `ui_server.py:184` with `self._get_branch()`. Keep `_unreachable_instance` unchanged.

**Anchors**: D-6, D-7, D-8, SC-6, SC-7, SC-12.

**BDD**:
```
Scenario: branch detection succeeds in a real git repo
  Given a temp dir initialized as a git repo with branch "feat/foo"
  When ui = UIServer(broker=stub, cwd=tmp_dir)
    and branch = ui._get_branch()
  Then branch == "feat/foo"

Scenario: branch detection falls back to "main" when git fails
  Given a temp dir that is NOT a git repo
  When ui = UIServer(broker=stub, cwd=tmp_dir)
    and branch = ui._get_branch()
  Then branch == "main"
  And no exception was raised

Scenario: branch cache honors the 30s TTL
  Given a UIServer pointing at a git repo
  When ui._get_branch() is called once (returns "feat/x")
    and _detect_branch is then patched to return "feat/y"
    and ui._get_branch() is called again immediately
  Then it still returns "feat/x" (cached)
  When time advances past _BRANCH_TTL_SECONDS
    and ui._get_branch() is called
  Then it returns "feat/y" (refreshed)

Scenario: subprocess timeout falls back to main
  Given a _detect_branch implementation that hangs
  When ui._get_branch() is called
  Then it returns "main" within ~2 seconds
  And subprocess.TimeoutExpired was caught

Scenario: branch surfaces in the dashboard payload
  Given a UIServer with cwd pointing at the claude-crew repo (git branch known)
  When _build_state() is called via the public path
  Then the local instance dict has branch == self._get_branch()
  And _unreachable_instance dicts (peer placeholders) still have branch == "main"
```

**Tests**: `test_branch_detection_succeeds_in_git_repo`, `test_branch_detection_falls_back_to_main_on_error`, `test_branch_cache_ttl_honors_30s_window`, `test_branch_subprocess_timeout_falls_back_to_main`, `test_unreachable_instance_branch_unchanged`.

**Verification**: `uv run pytest tests/test_ui_server.py -v -k "branch"`.

**Dependencies**: T1 (uses snapshot path), T2 (relies on the `_build_local_instance(snapshot)` signature). Can run parallel with T2 in principle, but linear chain is cleaner.

---

### Task T4 — Remove dead `_get_redaction_version()` ImportError fallback

**Goal**: delete `_get_redaction_version()` function in `claude_crew/teammate.py` (lines 50-62). Add module-level `from claude_crew.redaction import REDACTION_VERSION`. Replace both call sites (lines 154, 205) with the bare `REDACTION_VERSION` symbol.

**Anchors**: D-9, SC-8.

**BDD**:
```
Scenario: redaction_version is still surfaced in status_snapshot
  Given a Teammate (Stub or Sdk)
  When I call status_snapshot()
  Then snap["redaction_version"] == "v1"  (unchanged behavior)

Scenario: _get_redaction_version is no longer defined
  When import claude_crew.teammate
  Then teammate._get_redaction_version raises AttributeError
  (Or grep claude_crew/teammate.py for "_get_redaction_version" returns no matches)
```

**Tests**: extend an existing snapshot test to assert `redaction_version == "v1"` (likely already there); add `test_redaction_version_function_removed` as the cleanup guard.

**Verification**: `uv run pytest tests/test_teammate.py tests/test_stub_teammate.py -v`.

**Dependencies**: none — fully isolated. Can run in parallel with anything.

---

### Task T5 — E2E integration + dashboard sanity

**Goal**: cohesive end-to-end test that exercises the full assembled feature: spawn a real teammate, drive a turn, call `_build_state()`, verify the resulting dashboard payload is structurally correct and contains real values for cost (F14), tokens (F14), and branch (T3). Also verify the `_unreachable_instance` shape stays correct after the refactor.

**Anchors**: SC-9 (no regression), SC-12 (multi-instance), full pipeline.

**BDD**:
```
Scenario: full E2E — broker → snapshot → UIServer → dashboard payload
  Given a Broker with 2 alive teammates that have completed a turn each
  And a UIServer pointing at the claude-crew repo
  When state = await ui._build_state()
  Then state["instances"][0]["agents"] has length 2 with real cost/tokens
    and state["instances"][0]["branch"] != "main" (it's the actual git branch — likely "feature/broker-snapshot-dashboard-polish")
    and state["instances"][0]["cost"] is the sum of agent costs
    and state["transcripts"][crew_id] has the recent envelopes

Scenario: F14 tombstone aggregate still works post-refactor
  Given a broker with 1 alive teammate ($0.05) and 1 tombstoned ($0.20)
  When ui._build_local_instance(snapshot) is called
  Then instance["cost"] == 0.25 (alive snap + at_death)
  And instance["agents"] has length 1 (alive only)

Scenario: _unreachable_instance shape preserved (multi-instance compat)
  Given a registry pointing at a peer that returns 500
  When ui._build_state() is called
  Then the unreachable peer's instance dict has branch == "main"
    and is_local == False
    and status == "unreachable"
```

**Tests**: `test_e2e_dashboard_payload_with_real_branch`, `test_e2e_tombstone_aggregate_preserved`, `test_e2e_unreachable_instance_shape_preserved`. New file `tests/test_e2e_broker_snapshot.py`.

**Verification**: `uv run pytest tests/test_e2e_broker_snapshot.py -v` plus full suite.

**Dependencies**: T1, T2, T3, T4.

---

### Task summary table

| # | Task | SCs | Tests | Depends on |
|---|---|---|---|---|
| T1 | BrokerSnapshot types + Broker.snapshot() | SC-1, SC-3, SC-4, SC-5, SC-11, SC-13 | 6 | — |
| T2 | UIServer consumes snapshot | SC-2, SC-10 | 3 | T1 |
| T3 | Branch detection (TTL cache) | SC-6, SC-7, SC-12 | 5 | T1, T2 |
| T4 | Redaction inline cleanup | SC-8 | 2 | — (parallel-able) |
| T5 | E2E + dashboard sanity | SC-9 + full pipeline | 3 | T1-T4 |

**Sentinel checkpoint**: after T2 (the design-density boundary). T3+T4 are mechanical and run without per-task review.

**Gate**:
- ✅ 5 tasks, each independently testable
- ✅ Dedicated E2E test task with happy + sad path coverage (T5)
- ✅ Dependencies clear (mostly linear; T4 is independent)
- ✅ Verification commands fail without the feature
- ✅ Every Phase 1 SC traced to at least one BDD scenario
- ⏳ User approval to proceed to Phase 4

---

## Phase 4: Implementation

*Execution driven by SKILL.md. Update status in header as tasks complete.*

**Gate**: All tasks complete, quality gates passing, review done.

---

## Phase 5: Completion

### Verification
- [x] Feature works against Phase 1 success criteria — final sentinel review traced all 13 SCs to passing tests
- [x] No regressions — 530 passed, 10 skipped (was 511 + 9 pre-feature)
- [x] Spec updated to match implementation — D-2 deepcopy resolution recorded; A-2 wording corrected mid-feature
- [x] Docs updated — PRODUCT-VISION feature pipeline status, journal entry; BACKLOG entries for two adjacent gaps surfaced during the session
- [x] Manual test passed — operator-confirmed (dashboard works, branch detection live, no regressions)

### Retrospective

**What went well**:

- **Two parallel-eligible tasks identified up-front.** T4 (redaction inline cleanup) shared no files with T1-T3 and ran concurrent with T1 from kickoff. Saved a serial step at zero coordination cost. Pattern worth reusing: at Phase 3, mark each task with shares-files-with so parallel-eligibility is an explicit field, not an inferred one.
- **Co-architect hot-restart with onboarding delegation guidance.** Mid-feature, the persistent Opus co-architect was burning ~$0.30/turn from cumulative context (1.3M input tokens across 6 turns). Killed and respawned with a one-line operating-style instruction in the spawn prompt ("delegate raw file reads to explorer subagents"). Per-review token use dropped 83%, cost halved. Process improvement worth carrying forward.
- **Sentinel chain caught two real issues mid-feature.** Phase 2 review caught the D-2 inner-dict reference leak (test as written would have passed even with `dict(status)`); post-T2 sentinel had nothing to flag because the Phase 2 fix held. Correct gates landed at the correct boundaries.
- **Forward-compat field reservation worked.** SC-13 / D-10 reserved `tool_events: tuple = ()` for #19. Trivial in #18; means #19 doesn't need a snapshot v2.

**What was friction**:

- **Hidden read site found in Phase 2, not Phase 1 recon.** The Haiku explorer's recon listed 4 production reads in `ui_server.py`. Phase 2 synthesis surfaced a fifth: `teammate._model` at line 122. Lesson: Phase 1 recon should be told to grep for `(broker|teammate)\._\w+` patterns explicitly, not just "private attr reads on the broker." The same pattern would have caught it.
- **Pack-file teammate vs subagent prompt asymmetry surfaced as a side discovery.** Investigating why the co-architect wasn't delegating revealed that teammate system prompts are a generic 8-word default — pack file content is never used for top-level teammates. This is a structural gap (now Feature #21) that #18 didn't cause, but #18's session is what surfaced it. The "operate, observe gaps, log them" loop continues to find architectural friction nobody planned to look for.
- **Cumulative session cost on persistent crew teammates.** Even with prompt caching, persistent Opus teammates handling many turns get expensive. F14 telemetry made this visible and quantifiable; #18 forced a process change. Operational guidance, not a bug.

**Improvements**:

1. **Pre-Phase-3 grep prompt**: when commissioning Phase 1 recon for a refactor that targets "private API leakage," ask for `(<class_or_object>)\._\w+` matches explicitly, not just "reads of private attrs on X." Catches sibling-class leaks (like `teammate._model` slipping through a "broker private attrs" recon).
2. **Phase 3 task field for parallel-eligibility**: add a `shares-files-with: [task-id, ...]` row to each task. Tasks with empty shares-files-with can be dispatched in parallel from Phase 4 kickoff. Removes the "is this safe to parallelize?" judgment call at execution time.
3. **Co-architect onboarding template**: the "delegate raw file reads to explorer" guidance is general and should be in any persistent-teammate spawn prompt by default. Bake into a reusable prompt template until Feature #21 lands the structural fix.

**Workflow updates made**:
- [x] BACKLOG.md updated — three #18 follow-up entries (teammate/subagent prompt parity, persistent-teammate session cost, pack-model spawn asymmetry was already there from earlier)
- [x] PRODUCT-VISION.md updated — pipeline #18 marked done, #21 (teammate prompt parity) added as new feature
- [ ] `.claude/rules/` — no new project rule needed
- [ ] MEMORY.md — no cross-project insight; #18's lessons are claude-crew-specific

**Gate**: ✅ Feature verified end-to-end, manual test passed, retrospective captured, three actionable workflow improvements logged.
