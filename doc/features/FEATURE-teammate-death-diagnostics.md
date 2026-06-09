# Feature: Teammate Death Diagnostics

**Status**: Complete (merged — cycle 0)
**Created**: 2026-06-09
**Slug**: `teammate-death-diagnostics`
**Vision row**: PRODUCT-VISION.md → Post-MVP Substrate (liveness / observability thread)
**Motivating incident**: `rr-slice-reviewer` died 3 times in the `agent-pack-refresh` run; root cause undiagnosable because stderr was discarded — logged in `doc/BACKLOG.md` [2026-05-24].

---

## Spec

<!-- Full verbatim spec body. Source: .rr/specs/teammate-death-diagnostics.md -->

# Spec: teammate-death-diagnostics

## Problem

When an SDK teammate dies (subprocess exits unexpectedly, `ProcessError` /
`CLIConnectionError` / `BrokenPipeError` raised by the transport), the operator
sees only the exception class on the dashboard and in the transcript. There is
no stderr capture, no record of the last tool the teammate was running, and no
in-flight tool context attached to the death record. Post-mortem diagnosis
requires guessing.

The motivating incident: during the `agent-pack-refresh` repo-react run, the
`rr-slice-reviewer` teammate died three times. Root cause was undiagnosable
because `_handle_one_turn` discards the subprocess's stderr entirely
(`stderr=asyncio.subprocess.DEVNULL` in the SDK transport). The operator saw
`"exit_code": 1` and a `ProcessError` but nothing about what the teammate was
doing when it died.

This feature adds three additive telemetry components:

1. **Stderr ring buffer** — a bounded, byte-capped in-memory ring on
   `SdkTeammate` that captures the tail of the subprocess's stderr stream via
   an SDK-registered callback, with redaction before any persist step.
2. **Death-record fields** — two new fields on `TeammateInfo`
   (`stderr_tail_at_death`, `in_flight_tools_at_death`) populated at tombstone
   time from the teammate's last snapshot, threaded through to the
   dashboard/transcript payload.
3. **Death-site WARNING** — a structured `logging.WARNING` at the
   `ProcessError`/`CLIConnectionError`/`BrokenPipeError` catch arm in
   `_handle_one_turn` carrying the exception class, exit code, last tool
   completed, and redacted stderr tail.

No control-flow change. No new external dependencies. Purely additive.

## Architecture Overview

Three additive changes across two modules, plus a gated live wiring test:

1. **`claude_crew/sdk_teammate.py`** — add a bounded, byte-capped stderr ring
   buffer to `SdkTeammate`, register an SDK `stderr` callback that appends to
   it, expose a redacted tail on `status_snapshot()`, and emit a WARNING at the
   existing death-site catch arm.
2. **`claude_crew/broker.py`** — add two death-record fields to `TeammateInfo`
   (`stderr_tail_at_death`, `in_flight_tools_at_death`), populate them in
   `_tombstone_teammate` from the teammate's snapshot, and serialize both keys
   onto the dead-teammate status payload that feeds the dashboard/transcript.
3. **`tests/`** — implementation-layer tests (stub/unit) for the ring buffer,
   the death-record attachment, and the death-site WARNING, plus one gated live
   SDK test that proves the stderr callback is wired end-to-end during an
   ordinary turn (no forced crash required).

No architecture doc existed for this repo prior to this feature (`doc/ARCHITECTURE.md`
absent); the module roles are taken from the project `CLAUDE.md`.

### SDK behavior — stated as given facts (the SDK is a read-only dependency)

- `ClaudeAgentOptions` accepts an optional `stderr` kwarg that, when set to a
  callable, is invoked by the transport's `_handle_stderr` async task once per
  decoded stderr line from the subprocess.
- `opts_kwargs["stderr"] = self._on_stderr_line` is the registration point
  (`claude_crew/sdk_teammate.py`, `_run` method).
- The callback fires on the event loop; it must never raise (an exception would
  terminate the transport's `_handle_stderr` coroutine, silencing all future
  stderr lines for that session).
- The Claude CLI subprocess emits **no bytes to stderr during normal turns**.
  All output, including verbose/debug messages, routes to stdout as a JSON
  stream. Verified empirically: `subprocess.Popen` with `stderr=PIPE` on
  `claude --output-format stream-json --verbose` produced 0 stderr bytes. The
  ring buffer therefore only populates during error/crash scenarios.

### Call-site survey

- `opts_kwargs` assembled in `SdkTeammate._run` before `ClaudeAgentOptions(...)` call.
- `_tombstone_teammate` in `broker.py` (~line 927–958 at time of spec): step 4
  reads the teammate snapshot; step 5 calls `dataclasses.replace` with new
  fields. The snapshot read must occur before `_close_open_tools` abandons
  in-flight tools (ordering is load-bearing for `in_flight_tools_at_death`).
- `_handle_one_turn` in `sdk_teammate.py` (~line 1479–1491 at time of spec):
  the `ProcessError`/`CLIConnectionError`/`BrokenPipeError` catch arm is the
  target for the death-site WARNING injection.

## Data / API Contracts

### `SdkTeammate` (new fields + methods)

```python
# Module-level constants
_STDERR_RING_MAXLEN: int = 50          # max lines retained
_STDERR_RING_BYTE_CAP: int = 65_536   # 64 KB total byte budget

# __init__ additions
self._stderr_ring: deque[str] = deque(maxlen=_STDERR_RING_MAXLEN)
self._stderr_ring_bytes: int = 0

# New callback registered as opts_kwargs["stderr"]
def _on_stderr_line(self, line: str) -> None:
    """Append one stderr line to the ring; never raises."""
    ...  # dual eviction: by count (deque maxlen) and by bytes

# New helper consumed by death-site WARNING and snapshot
def _stderr_tail_redacted(self) -> str | None:
    """Join ring lines, run redact_output, return None when empty."""
    ...

# status_snapshot() additions (new keys on existing dict)
snap["stderr_tail"] = self._stderr_tail_redacted()   # str | None
snap["in_flight_tools"] = list(snap.get("current_tools", []))  # list[dict]
```

### `TeammateInfo` (new death-record fields — `broker.py`)

```python
@dataclass
class TeammateInfo:
    ...
    # New fields (keyword-defaulted None; backward-compatible)
    stderr_tail_at_death: str | None = None
    in_flight_tools_at_death: list[Any] | None = None
```

**Serialization**: both fields appear on the dead-teammate status dict under the
same key names. Additive — existing consumers see new keys, no existing key
changes.

### `_tombstone_teammate` step 4 population contract

```python
# Read snapshot BEFORE _close_open_tools abandons in-flight tools
try:
    snap = teammate.status_snapshot()
    stderr_tail_at_death = snap.get("stderr_tail")
    in_flight_raw = snap.get("in_flight_tools")
    in_flight_tools_at_death = list(in_flight_raw) if in_flight_raw is not None else []
except AttributeError:
    stderr_tail_at_death = None
    in_flight_tools_at_death = None
```

**None-vs-`[]` semantics**:
- `None`: snapshot could not be read (teammate is `None`, snapshot raised `AttributeError`)
- `[]`: snapshot readable but no tool was in flight at death
- `[{...}, ...]`: tools that were in flight when the snapshot was taken

### Death-site WARNING format (`_handle_one_turn`)

```python
logger.warning(
    "teammate %s died: exc=%s exit_code=%s last_tool=%s stderr_tail=%s",
    self._teammate_id,
    type(exc).__name__,
    getattr(exc, "exit_code", None),
    self._last_tool_completed,
    self._stderr_tail_redacted(),
)
# Unchanged control flow follows: _death_suspected / _death_in_flight_envelope / return
```

## Design Decisions

1. **Ring bounded by both line count AND bytes**: The byte cap (`_STDERR_RING_BYTE_CAP=65536`) prevents pathological cases where 50 very long lines exhaust memory. Eviction is oldest-first: when adding a line would exceed the byte cap, oldest entries are popped until the cap is satisfied before appending. The `deque(maxlen=50)` handles line-count eviction automatically.

2. **Never-raising callback**: If `_on_stderr_line` raises, the SDK transport's `_handle_stderr` coroutine would terminate silently, losing all future stderr for the session. The method uses `try/except Exception: return` to guarantee this invariant. This is documented in the method docstring.

3. **Redaction before any persist**: `_stderr_tail_redacted()` runs `redact_output` on the joined ring contents before returning. The raw ring contents never leave `SdkTeammate` — neither the snapshot, the WARNING, nor the death-record fields carry unredacted secrets. `redact_output` is already imported in `sdk_teammate.py` (used by the F8 hook path).

4. **`except AttributeError` as the graceful path**: If the teammate reference is `None` or `status_snapshot` doesn't exist (stub teammate, future refactor), the except arm sets both fields to `None` and the tombstone still completes. The tombstone path stays diagnosable regardless of snapshot availability.

5. **Snapshot ordering**: The snapshot read in `_tombstone_teammate` occurs at step 4, before `_close_open_tools` at step 6 abandons in-flight tools. If the snapshot were read after, `in_flight_tools` would always be `[]`.

6. **Live test strategy (no forced crash)**: AT-8 proves the SDK callback registration is in place during an ordinary live turn. It does not require the teammate to crash. The registration proof uses `client.options.stderr` introspection — asserting the bound method's `__self__` and `__func__` attributes — which is sensitive to the exact line that sets `opts_kwargs["stderr"]`. A mutation test (delete line 1432 → RED, restore → GREEN) was run by the reviewer to confirm the assertion is genuinely regression-sensitive.

## Edge Cases

- **Empty ring at death**: `_stderr_tail_redacted()` returns `None`. `stderr_tail_at_death` is `None`. Normal — means the CLI never wrote to stderr.
- **`in_flight_tools` key absent from snapshot**: `snap.get("in_flight_tools")` returns `None`; the broker records `in_flight_tools_at_death = []` (empty list, not `None`) because the snapshot was readable — no tool was in flight.
- **Snapshot raises `AttributeError`**: Both fields set to `None`. Tombstone completes; `alive=False`. The None value signals "snapshot unavailable at death time."
- **Ring eviction mid-death**: The ring is owned by `SdkTeammate`; it is read by `_stderr_tail_redacted()` at snapshot time, which is before the tombstone. No concurrency concern: snapshot is called from the broker's `_tombstone_teammate`, which runs in the same event loop as the ring appender. The ring snapshot is a point-in-time read.
- **Redaction of ring content**: `redact_output` may alter the tail string. The redacted value is what reaches the snapshot, WARNING, and death record. The raw ring is never serialized.

## Acceptance Tests

1. **Ring buffer bounded (line + byte).** `_on_stderr_line` called 120 times → `len(t._stderr_ring) == 50`; ring holds last 50 lines, not first 50.
2. **Ring buffer byte cap.** `_on_stderr_line` called with lines exceeding 64 KB total → byte cap enforced; oldest lines evicted; `_stderr_ring_bytes <= _STDERR_RING_BYTE_CAP`.
3. **`_stderr_tail_redacted` redacts secrets.** Ring populated with a line containing an API key pattern → `_stderr_tail_redacted()` returns a string with the key replaced by the redaction token; never `None`.
4. **`status_snapshot` exposes both keys.** After populating the ring, `status_snapshot()` returns a dict containing `stderr_tail` (str) and `in_flight_tools` (list).
5. **Death record attaches stderr tail and in-flight tools.** Monkeypatched snapshot returns `stderr_tail="line A\n<redacted-key>"` and `in_flight_tools=[{"tool_name": "Bash", "args_summary": "command=…"}]` → after `_handle_teammate_death`, dead-teammate status has `alive=False`, `stderr_tail_at_death=="line A\n<redacted-key>"`, `in_flight_tools_at_death==[{"tool_name": "Bash", "args_summary": "command=…"}]`.
6. **Death record graceful when no stderr / no snapshot.** (a) Snapshot returns `stderr_tail=None`, no `in_flight_tools` key → `stderr_tail_at_death is None`, `in_flight_tools_at_death == []`. (b) `status_snapshot()` raises `AttributeError` → both fields `None`, tombstone completes (`alive=False`).
7. **Death-site WARNING.** `_stderr_tail_redacted()` returns `"tail-marker"`, `_last_tool_completed="Bash"`, `ProcessError` raised with `exit_code=2` → `logging.WARNING` logged containing `exc_class=ProcessError`, `exit_code=2`, `last_tool=Bash`, `stderr_tail=tail-marker`; control flow unchanged (no extra exception, same return path).
8. **Live end-to-end stderr callback registration.** Gated (`CLAUDE_CREW_LIVE_TESTS=1`): real `SdkTeammate` spawned via `broker.spawn_teammate(factory=sdk_factory)`, ordinary turn run, `client.options.stderr` introspected — asserts `registered is not None`, `registered.__self__ is tm`, `registered.__func__ is SdkTeammate._on_stderr_line`. Ring-to-snapshot smoke: `_on_stderr_line` injected with probe line, `status_snapshot()` confirms probe in `stderr_tail`.

## Out of Scope

- **Crash prevention or death-rate reduction** — this feature is diagnostics only; it does not change when or why a teammate dies.
- **`graceful-flush` arm WARNING** — the secondary `exc_name` in the retry/flush arm is intentionally excluded; only the primary death-path catch arm is in scope.
- **Dashboard rendering of `stderr_tail_at_death`** — the field lands in the status payload; dashboard display of it is a follow-on UX task.
- **Redaction versioning** — `redact_output` is called as-is; schema versioning of the redacted tail is out of scope.
- **Persistent stderr storage** — the ring is in-memory only and is lost when the `SdkTeammate` object is garbage-collected after death.

## Task Breakout

- **`stderr-ring-buffer`** (ATs 1-4): ring constants, `__init__` fields, `_on_stderr_line`, `_stderr_tail_redacted()`, `opts_kwargs["stderr"]` registration in `_run`, `status_snapshot()` additions. Touches `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py`.
- **`death-record-fields`** (ATs 5-6): `TeammateInfo` fields, `_tombstone_teammate` step 4 population, `dataclasses.replace` threading, status payload serialization. Depends on `stderr-ring-buffer`. Touches `claude_crew/broker.py`, `tests/test_broker.py`.
- **`death-site-warning`** (AT 7): WARNING at `ProcessError`/`CLIConnectionError`/`BrokenPipeError` catch arm. Depends on `stderr-ring-buffer` (consumes `_stderr_tail_redacted()`). Touches `claude_crew/sdk_teammate.py`, `tests/test_sdk_teammate.py`.
- **`live-stderr-wiring-test`** (AT 8): gated live test confirming SDK callback registration. Touches `tests/test_live_stderr.py` (new file).

---

## What Shipped

+84 production lines across `claude_crew/sdk_teammate.py` (+64) and `claude_crew/broker.py` (+20). +340 test lines across `tests/test_sdk_teammate.py`, `tests/test_broker.py`, and new `tests/test_live_stderr.py`.

**`claude_crew/sdk_teammate.py`**:
- `_STDERR_RING_MAXLEN = 50`, `_STDERR_RING_BYTE_CAP = 65536` module constants
- `_stderr_ring: deque[str]`, `_stderr_ring_bytes: int` in `__init__`
- `_on_stderr_line(line: str) -> None` — never-raising callback; dual line/byte eviction
- `_stderr_tail_redacted() -> str | None` — joins ring, runs `redact_output`, returns `None` when empty
- `opts_kwargs["stderr"] = self._on_stderr_line` in `_run` (line 1432)
- `status_snapshot()` additions: `snap["stderr_tail"]`, `snap["in_flight_tools"]`
- `logging.WARNING` at ProcessError/CLIConnectionError/BrokenPipeError catch arm in `_handle_one_turn`

**`claude_crew/broker.py`**:
- `TeammateInfo.stderr_tail_at_death: str | None = None`
- `TeammateInfo.in_flight_tools_at_death: list[Any] | None = None`
- `_tombstone_teammate` step 4: snapshot read + field population (before `_close_open_tools`)
- `dataclasses.replace` threading at step 5
- Both keys serialized onto dead-teammate status payload

**`tests/test_live_stderr.py`** (new gated file):
- `TestLiveStderrWiring.test_stderr_ring_populated_after_ordinary_turn` (AT 8)
- Module-level `pytestmark` gate (`CLAUDE_CREW_LIVE_TESTS=1`)
- Registration proof via `client.options.stderr` introspection (`__self__`/`__func__` decomposition)
- Ring-to-snapshot smoke: `_on_stderr_line` injection + `status_snapshot()` assertion

---

## Feature Review Summary

No Critical, High, Medium, or Low findings. Feature-review confirmed:

- **Cross-slice integration coherence**: `status_snapshot()` keys `stderr_tail`/`in_flight_tools` match broker read-side byte-for-byte. None-vs-`[]` semantics correct end-to-end. Same-file co-tenancy (`stderr-ring-buffer` + `death-site-warning` in `sdk_teammate.py`) composes without clobber; WARNING consumes `_stderr_tail_redacted()` and `_last_tool_completed` — wired, not merely co-resident.
- **Holistic spec satisfaction**: all 8 ATs covered; snapshot-ordering invariant (read before `_close_open_tools`) confirmed.
- **No cracks**: in-flight capture ordering correct; WARNING scope boundary correct (primary arm only); no behavior regression.
- **Mandatory non-regression**: full suite 1322 passed, 34 skipped, 1 xfailed (exit 0). Two pre-existing `test_shutdown_signals.py` failures reproduced identically on master — disjoint surface, not feature-caused.

---

## Retro Findings

| finding-id | origin | severity | disposition | notes |
|------------|--------|----------|-------------|-------|
| HIGH-01 | slice-review-0 (live-stderr-wiring-test) | High | addressed (cycle 1) | Live test was blind to SDK registration at line 1432; `client.options.stderr` introspection added; mutation test confirms RED on delete-line-1432, GREEN on restore. |
| MED-01 | slice-review-0 (live-stderr-wiring-test) | Medium | addressed (cycle 1) | Docstring falsely claimed method accessibility proved registration; rewritten to correctly separate registration proof from ring smoke. |
| LOW-01 | slice-review-1 (live-stderr-wiring-test) | Low | addressed-at-merge | Inline `from claude_crew.sdk_teammate import SdkTeammate` hoisted to module top by coordinator before merge. |
| BC-01 | build-0 (live-stderr-wiring-test) | Low | routed to backlog | "Claude CLI emits no bytes to stderr during normal turns" — verified SDK invariant; routed to CLAUDE.md `doc-update` as BC-01 in retro backlog candidates. |

---

## Backlog Deltas

Backlog additions gated on coordinator signoff. See `teammate-death-diagnostics-retro-backlog-candidates.md` (BC-01).

No items closed from the existing backlog by this feature. The motivating incident entry ([2026-05-24] "SDK teammate dies (exit 1 / 'no text content') on reuse — and we can't see why") is now *partially* addressed: stderr is now captured and attached to the death record. The entry may be updated at coordinator discretion to note partial resolution.

---

## Validation Summary

Full suite: `uv run pytest` → **1322 passed, 34 skipped, 1 xfailed, 42 warnings** in 136s (exit 0).

Gated live test: `CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py` → **1 passed** (5.39s).

Mutation gate (reviewer-run): delete `sdk_teammate.py:1432` → live test RED with `AssertionError: client.options.stderr is None`; restore → GREEN.

---

## Commits

TBD — commit hash assigned at merge signoff.
