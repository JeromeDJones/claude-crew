# Spec: teammate-death-diagnostics

## Problem

When an SDK teammate dies — the node `claude` CLI subprocess exits non-zero and
the SDK raises `ProcessError` during the response drain — claude-crew today
emits an opaque envelope to the lead (`"...crashed: Command failed with exit
code 1"`) and keeps **no record of why**. The subprocess stderr is inherited
into the server's own stream and lost, the death-site catch arm returns
silently with zero logging, and nothing captures what the teammate was doing
at the moment of death. An operator (or the coordinator) reading the dashboard
or transcript after the fact cannot tell why the teammate died. The
user-visible outcome of this feature: **the next organic teammate death is
self-explaining** — the death record carries the last ~50 redacted lines of CLI
stderr, the last tool the teammate ran (especially the last Bash command and
its outcome) plus any tool in flight at death, and the death site emits a
WARNING with the exception class, exit code, stderr tail, and last-tool
context. This is diagnostic telemetry only; it deliberately does **not** change
death/tombstone control flow or attempt to prevent the death.

## Architecture Overview

Three additive changes across two modules, plus a gated live wiring test:

1. **`claude_crew/sdk_teammate.py`** — add a bounded, byte-capped stderr ring
   buffer to `SdkTeammate`, register an SDK `stderr` callback that appends to
   it, expose a redacted tail on `status_snapshot()`, and emit a WARNING at the
   existing death-site catch arm.
2. **`claude_crew/broker.py`** — add two death-record fields to `TeammateInfo`
   (`stderr_tail_at_death`, `in_flight_tools_at_death`), populate them in
   `_tombstone_teammate` from the teammate's snapshot, and serialize them onto
   the dead-teammate status payload that feeds the dashboard/transcript.
3. **`tests/`** — implementation-layer tests (stub/unit) for the ring buffer,
   the death-record attachment, and the death-site WARNING, plus one gated live
   SDK test that proves the stderr callback is wired end-to-end during an
   ordinary turn (no forced crash required).

No architecture doc exists for this repo (`doc/ARCHITECTURE.md` absent); the
module roles above are taken from the project `CLAUDE.md`.

### SDK behavior — stated as given facts (the SDK is a read-only dependency)

The implementor is a local model with no prior knowledge of
`claude-agent-sdk`. The following are **facts about the dependency**, not things
to investigate or change. File:line anchors are into
`.venv/lib/python3.12/site-packages/claude_agent_sdk/` (DO NOT edit the SDK):

- `ClaudeAgentOptions` has a field `stderr: Callable[[str], None] | None`. When
  it is **not None**, the transport pipes the subprocess stderr and invokes the
  callback **once per stderr line** with the line string (no trailing newline
  guaranteed). When it is `None` (claude-crew's current state), stderr is
  inherited and lost. (`_internal/transport/subprocess_cli.py:447` —
  `stderr_dest = PIPE if self._options.stderr is not None else None`;
  `:493-506` — `_handle_stderr()` reads lines and calls
  `self._options.stderr(line_str)`.)
- The callback runs inside the SDK's stderr-reader asyncio task. If it raises,
  it can destabilize that task. It **must never raise** and must be cheap and
  non-blocking — just append.
- When the node CLI exits non-zero, the transport raises
  `ProcessError("Command failed with exit code N", exit_code=N)` during the
  `receive_response()` drain (`subprocess_cli.py:668-676`). The exception
  object carries `.exit_code` but `.stderr` is **None** — the transport does
  not populate it. Therefore the only way to know *why* is to have captured
  stderr ourselves via the callback above. (`_errors.py:25-40`.)

### Call-site survey

| Call-site | Path | Shape | Notes |
|-----------|------|-------|-------|
| Options construction | `claude_crew/sdk_teammate.py` `_run`, `opts_kwargs` dict built ~line 1243, `ClaudeAgentOptions(**opts_kwargs)` ~line 1376 | builds the options kwargs dict | The single place the `stderr` callback is registered. |
| Death-site catch arm | `claude_crew/sdk_teammate.py` `_handle_one_turn`, generic `except Exception as exc:` ~lines 1479-1491 | matches `ProcessError`/`CLIConnectionError`/`BrokenPipe` by class name; sets `_death_suspected=True`; returns silently | The single place the death-site WARNING is added. A second structurally-identical arm exists in `_run_flush_turn` ~lines 1599-1603 (graceful-flush path); see Edge Cases. |
| Tombstone capture | `claude_crew/broker.py` `_tombstone_teammate` ~lines 366-534, snapshot read at step 4 (~417-442) | reads `teammate.status_snapshot()` BEFORE `_close_open_tools` (step 8b) | The single place the new death-record fields are captured. |

Resolution: each concern has exactly one primary call-site; no polymorphic
helper is needed. The flush-path death arm is handled by reusing the same
WARNING helper (see Design Decisions) so the two arms do not drift.

## Data / API Contracts

```python
# --- claude_crew/sdk_teammate.py : SdkTeammate.__init__ (new fields) ---
# Add immediately after the existing `self._client = ... None` line (~693).
import collections  # already imported at module top

_STDERR_RING_MAXLEN: int = 50          # module-level constant near other tunables
_STDERR_RING_BYTE_CAP: int = 65536     # 64 KiB hard byte ceiling

self._stderr_ring: collections.deque[str] = collections.deque(maxlen=_STDERR_RING_MAXLEN)
self._stderr_ring_bytes: int = 0       # running UTF-8 byte total of ring contents

# --- new method: cheap, non-blocking, NEVER raises ---
def _on_stderr_line(self, line: str) -> None:
    """SDK stderr callback. Appends one CLI stderr line to the ring buffer.

    Registered as ClaudeAgentOptions.stderr. Runs inside the SDK's stderr
    reader task — must never raise (a raising callback destabilizes that task)
    and must be cheap (just append + trim). Bounded by maxlen (line count) AND
    _STDERR_RING_BYTE_CAP (byte total): oldest lines are evicted when either
    bound is exceeded.
    """
    try:
        if line is None:
            return
        s = line if isinstance(line, str) else str(line)
        nbytes = len(s.encode("utf-8", errors="ignore"))
        # If a single line alone exceeds the cap, store it but it is the only
        # element after trimming below.
        if len(self._stderr_ring) == self._stderr_ring.maxlen and self._stderr_ring:
            # deque is full; appending evicts the oldest — adjust byte total.
            self._stderr_ring_bytes -= len(
                self._stderr_ring[0].encode("utf-8", errors="ignore")
            )
        self._stderr_ring.append(s)
        self._stderr_ring_bytes += nbytes
        # Byte-cap trim: evict oldest until under cap (keep at least one line).
        while self._stderr_ring_bytes > _STDERR_RING_BYTE_CAP and len(self._stderr_ring) > 1:
            evicted = self._stderr_ring.popleft()
            self._stderr_ring_bytes -= len(evicted.encode("utf-8", errors="ignore"))
    except Exception:
        # Never propagate out of the SDK stderr reader task.
        return

# --- new method: redacted tail for persistence ---
def _stderr_tail_redacted(self) -> str | None:
    """Return the redacted, joined ring contents, or None if empty.

    Joins ring lines with "\n" and runs them through redaction.redact_output
    (V1 patterns + output-only patterns + 32 KiB cap). Returns None when the
    ring is empty. Never raises: on redaction failure returns the sentinel
    "[stderr-redaction-failed]" so the death path stays diagnosable and bounded.
    """
    if not self._stderr_ring:
        return None
    joined = "\n".join(self._stderr_ring)
    try:
        return redact_output(joined)   # redact_output already imported in module
    except Exception:
        return "[stderr-redaction-failed]"

# --- ClaudeAgentOptions registration in _run() ---
# In the opts_kwargs dict literal (~1243-1266), add ONE key:
opts_kwargs["stderr"] = self._on_stderr_line
# (equivalently set it just before `options = ClaudeAgentOptions(**opts_kwargs)`
#  at ~1376; either location is correct since opts_kwargs is the constructor arg.)

# --- status_snapshot() addition (~1130-1163, before `return snap`) ---
snap["stderr_tail"] = self._stderr_tail_redacted()   # redacted str or None
snap["in_flight_tools"] = list(snap.get("current_tools", []))  # already redacted args_summary


# --- claude_crew/broker.py : TeammateInfo new fields (~after line 83) ---
# Diagnostic death-record fields (teammate-death-diagnostics). None for alive.
stderr_tail_at_death: str | None = None
# Snapshot of in-flight tools (current_tools) captured at tombstone time,
# BEFORE _close_open_tools abandons them. Each entry carries redacted
# args_summary (so the last in-flight Bash command is visible). None if the
# teammate's snapshot could not be read; [] if no tool was in flight.
in_flight_tools_at_death: "list[dict[str, Any]] | None" = None


# --- broker.py : _tombstone_teammate step 4 capture (~417-442 try block) ---
# Inside the existing `try:` that reads `snap = teammate.status_snapshot()`:
stderr_tail_at_death: str | None = snap.get("stderr_tail")
in_flight_tools_at_death: list[dict[str, Any]] = list(snap.get("in_flight_tools", []))
# ...and in BOTH the `except AttributeError:` block (~443) and the
# `else:` (teammate is None, ~456) blocks, set:
#     stderr_tail_at_death = None
#     in_flight_tools_at_death = None

# --- broker.py : the dataclasses.replace at step 5 (~471) — add two kwargs ---
stderr_tail_at_death=stderr_tail_at_death,
in_flight_tools_at_death=in_flight_tools_at_death,

# --- broker.py : dead_result serialization (~927-958) — add two keys ---
"stderr_tail_at_death": info.stderr_tail_at_death,
"in_flight_tools_at_death": info.in_flight_tools_at_death,


# --- sdk_teammate.py : death-site WARNING (the catch arm ~1479-1491) ---
# Replace the silent body of the matched branch with a WARNING + the same
# silent control flow (additive — do NOT change _death_suspected / return):
exc_name = type(exc).__name__
if ("ProcessError" in exc_name
        or "CLIConnectionError" in exc_name
        or "BrokenPipe" in exc_name):
    exit_code = getattr(exc, "exit_code", None)
    logger.warning(
        "SDK teammate death detected: teammate=%s role=%s exc=%s exit_code=%s "
        "last_tool=%s stderr_tail=%s",
        self.id, self.role, exc_name, exit_code,
        self._last_tool_completed, self._stderr_tail_redacted(),
    )
    self._death_in_flight_envelope = env
    self._death_suspected = True
    return  # poll task tombstones; no envelope sent here (UNCHANGED)
```

## Design Decisions

- **Capture stderr via the SDK `stderr` callback, not by reading the process** — *Rationale:* the SDK only pipes stderr when `ClaudeAgentOptions.stderr` is non-None (`subprocess_cli.py:447`); registering a callback is the supported, documented hook and avoids touching the transport. — *Carried into:* `SdkTeammate._on_stderr_line`; `opts_kwargs["stderr"]` registration in `_run`; AT#8 (live wiring).
- **Ring buffer bounded by BOTH line count and byte total** — *Rationale:* a malformed teammate could emit a few enormous lines; `deque(maxlen=50)` bounds lines but not bytes, so a 64 KiB byte cap with oldest-eviction guarantees memory is bounded regardless of line shape. — *Carried into:* `_STDERR_RING_MAXLEN`, `_STDERR_RING_BYTE_CAP`, `_on_stderr_line` trim loop; AT#1.
- **Redact at read time, not at append time** — *Rationale:* the callback must be cheap and non-blocking (it runs in the SDK's stderr-reader task); redaction is comparatively expensive and only needed when we persist. The ring stores raw lines; `_stderr_tail_redacted()` redacts when the snapshot/death-record is built. — *Carried into:* `_on_stderr_line` (raw append) vs `_stderr_tail_redacted()` (redact via `redact_output`); AT#2.
- **All persisted stderr passes through `redaction.redact_output`** — *Rationale:* stderr can carry tokens/env; `redact_output` applies V1 + output-only patterns and caps to 32 KiB, the established path for redacting tool output bodies. — *Carried into:* `_stderr_tail_redacted()`; AT#2; AT#5.
- **Callback never raises** — *Rationale:* a raising stderr callback can destabilize the SDK's stderr-reader asyncio task. `_on_stderr_line` wraps its whole body in `try/except: return`. — *Carried into:* `_on_stderr_line`; AT#4.
- **Reuse existing tool-tracking — do not build a parallel tracker** — *Rationale:* `_last_tool_completed` (last clean Pre→Post pair) and `status_snapshot()["current_tools"]` (in-flight tools with redacted `args_summary`) already exist via the F8 hooks; the last in-flight Bash command is already captured there. — *Carried into:* WARNING uses `self._last_tool_completed`; `in_flight_tools_at_death` = snapshot `current_tools`; AT#5; AT#7.
- **Capture `in_flight_tools_at_death` at tombstone step 4, before `_close_open_tools`** — *Rationale:* `_close_open_tools` (step 8b) abandons in-flight tools; reading `current_tools` from the snapshot at step 4 preserves what the teammate was actually doing (the Bash command in flight) before it is abandoned. — *Carried into:* `_tombstone_teammate` step-4 capture; `TeammateInfo.in_flight_tools_at_death`; AT#5.
- **Additive only — no control-flow change** — *Rationale:* this is telemetry; the existing death/tombstone path, the `_death_suspected`/`_death_in_flight_envelope` handoff, and the error-envelope semantics are unchanged. The WARNING is added before the unchanged `return`; the new fields default to `None`. — *Carried into:* death-site arm keeps its `_death_suspected=True; return`; new `TeammateInfo` fields are keyword-defaulted `None`; AT#6 (graceful no-stderr).
- **Death-site WARNING emitted at the catch arm where the exception object lives** — *Rationale:* `exc.exit_code` is only available at the `except` arm (the liveness poll only has the process returncode, not the exception); logging there gives exc class + exit_code + stderr tail + last-tool in one line. — *Carried into:* the `logger.warning(...)` in the matched branch ~1483; AT#7.

## Edge Cases

- **Empty stderr ring (teammate never wrote stderr):** `_stderr_tail_redacted()` returns `None`; `status_snapshot()["stderr_tail"]` is `None`; death record `stderr_tail_at_death` is `None`; no crash. (AT#3, AT#6)
- **Secret-bearing stderr line** (e.g. `Authorization: Bearer sk-ant-api03-...`): the redacted tail must contain `<redacted>`/`<redacted-key>` and must NOT contain the raw secret. (AT#2)
- **Single line larger than the byte cap:** stored as the sole ring element after the trim loop (trim keeps ≥1 line); `redact_output`'s 32 KiB cap bounds the persisted tail. (AT#1)
- **More than 50 lines fed:** ring holds only the last 50 (deque maxlen); byte total tracks evictions. (AT#1)
- **Callback receives `None` or a non-str:** `_on_stderr_line` guards `None` (early return) and coerces non-str via `str()`; never raises. (AT#4)
- **Redaction raises inside `_stderr_tail_redacted()`:** returns the sentinel `"[stderr-redaction-failed]"` rather than propagating — the death path stays alive. (covered by AT#2 design; sentinel asserted not required)
- **`status_snapshot()` raises `AttributeError` during tombstone** (bare mock/old fixture teammate): the existing `except AttributeError` block sets both new fields to `None`; tombstone completes. (AT#6)
- **`teammate is None` at tombstone** (already popped / unknown): the existing `else` block sets both new fields to `None`. (AT#6)
- **Death via the graceful-flush arm** (`_run_flush_turn` ~1599-1603, second class-name match): out of scope for the WARNING this slice — the WARNING is added only to the primary `_handle_one_turn` arm. The new `TeammateInfo` fields are still populated because tombstoning always runs through `_tombstone_teammate`. (see Out of Scope)

**If this feature affects displayed data, answer these:**
- *Absent data (no stderr, no tool):* dashboard death row shows `stderr_tail_at_death: null` and `in_flight_tools_at_death: null` (or `[]`); the existing rendering already tolerates null death-record fields (e.g. `last_tool_completed: null`).
- *Capped data:* `stderr_tail_at_death` is the last ~50 lines / ≤32 KiB; older stderr is intentionally not retained.
- *Zero vs missing:* `in_flight_tools_at_death: []` means "snapshot read, no tool in flight"; `null` means "snapshot could not be read." Distinct and intentional.

**If this feature retires, expires, or caps data, answer these:**
- *Consumers of the dead-teammate status payload:* `claude_crew/ui_server.py` (dashboard `/api/state` aggregation) and transcript replay read the dict returned by the broker's per-teammate status method (`broker.py` ~900-963). They read keys dynamically; adding two keys is additive and does not break existing readers. The two new fields appear only on the **dead** payload branch (~927-958); the alive branch is unchanged.
- *Each consumer on a capped/expired record:* renders the new fields if present, ignores them if absent — no consumer asserts a fixed key set on the death payload.
- *Filtering/aggregation assuming "live":* none — the new fields are death-only and never read on the alive path.

## Acceptance Tests

1. **Ring buffer bounded (line + byte).** Given a fresh `SdkTeammate` (constructed via the test factory used in `tests/test_sdk_teammate.py`), when `_on_stderr_line` is called 120 times with distinct short lines, then `len(t._stderr_ring) == 50` and the ring holds the **last** 50 lines (line 120 present, line 70 absent). And when instead fed a small number of lines whose combined UTF-8 size exceeds `_STDERR_RING_BYTE_CAP` (64 KiB), then `t._stderr_ring_bytes <= _STDERR_RING_BYTE_CAP` after the calls and the ring still holds ≥1 line.
2. **Ring tail redacted.** Given a teammate fed a stderr line containing a secret (`"Authorization: Bearer sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"`), when `status_snapshot()` is read, then `snap["stderr_tail"]` is a non-empty string that does **not** contain `"sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG1234"` and **does** contain a redaction marker (`"<redacted"`).
3. **Empty stderr graceful.** Given a teammate that has received no stderr lines, when `status_snapshot()` is read, then `snap["stderr_tail"] is None` and no exception is raised; `snap["in_flight_tools"] == []`.
4. **Callback never raises.** Given a teammate, when `_on_stderr_line(None)` and `_on_stderr_line(12345)` (non-str) and `_on_stderr_line("ok")` are each called, then none raises and `"ok"` is present in `t._stderr_ring`.
5. **Death record attaches stderr tail + in-flight tools.** Given a stub-mode broker with a registered teammate whose snapshot reports `stderr_tail="line A\n<redacted-key>"` and `in_flight_tools=[{"tool_name": "Bash", "args_summary": "command=…"}]` (inject by monkeypatching/overriding the teammate's `status_snapshot`), when `_handle_teammate_death(teammate_id, exit_code=1)` runs, then the per-teammate status dict for that id has `alive == False`, `exit_code == 1`, `stderr_tail_at_death == "line A\n<redacted-key>"`, and `in_flight_tools_at_death == [{"tool_name": "Bash", "args_summary": "command=…"}]`.
6. **Death record graceful when no stderr / no snapshot.** Given a stub-mode broker with a registered teammate whose snapshot reports `stderr_tail=None` and no `in_flight_tools` key, when the teammate is tombstoned, then `stderr_tail_at_death is None` and `in_flight_tools_at_death == []`; and given a teammate whose `status_snapshot()` raises `AttributeError`, when tombstoned, then both fields are `None` and the tombstone still completes (`alive == False`).
7. **Death-site WARNING.** Given an `SdkTeammate` whose `_stderr_tail_redacted()` returns `"tail-marker"` and whose `_last_tool_completed` is `{"tool_name": "Bash", "outcome": "error"}`, when `_handle_one_turn` is driven so that `client.query`/drain raises an exception whose class name contains `"ProcessError"` and which carries `.exit_code = 1` (use a synthetic exception class named `ProcessError` with an `exit_code` attribute), then a `logging.WARNING` record is emitted whose message contains `"ProcessError"`, `"exit_code=1"`, `"tail-marker"`, and `"Bash"`; and `t._death_suspected is True` and `t._death_in_flight_envelope` is the in-flight envelope (control flow unchanged).
8. **Gated live wiring (CLAUDE_CREW_LIVE_TESTS=1).** Given a real `SdkTeammate` spawned via the live-SDK harness (skipped unless `CLAUDE_CREW_LIVE_TESTS=1`), when it runs one ordinary turn (a trivial prompt that completes normally, no forced crash), then after the turn the teammate's stderr ring is non-empty **or** `status_snapshot()["stderr_tail"]` is a string — proving the `stderr` callback was registered and invoked end-to-end. Honors the CLAUDE.md live-test conventions: bounded async-iterator drains and HOME-monkeypatch SDK-auth preservation (`_preserve_sdk_auth`).

## Test Command

All test files import only `pytest`, `asyncio`, `logging`, and `claude_crew.*`
modules — all already in `pyproject.toml` (no new dependencies). The live test
(AT#8) additionally drives `claude_agent_sdk`, already a project dependency, and
is skipped unless `CLAUDE_CREW_LIVE_TESTS=1`. No system-level setup is required
for the default (non-live) run. Install/sync deps first with `uv sync` if the
environment is fresh.

Per CLAUDE.md ("Validate the whole suite when changing widely-consumed
behavior"), this feature touches `broker._tombstone_teammate` and
`SdkTeammate`, both widely consumed, so the suite-level gate runs the **full**
suite:

```bash
uv run pytest
```

## Out of Scope

- Preventing or fixing the node CLI's non-zero exit — the distal cause is
  unknown; this telemetry is how we will learn it.
- Session-resume / retry-on-`ProcessError` resilience.
- The backlog's "catch the non-zero-exit tool error and feed it back as the
  tool result" idea — a misdiagnosis: claude-crew never sees individual tool
  results, and by the time `ProcessError` is caught the subprocess is already
  dead (no live model to feed).
- Dashboard redesign — surfacing the two new fields in the existing
  dead-teammate status rendering is sufficient; no new UI components.
- Adding the death-site WARNING to the **graceful-flush** death arm
  (`_run_flush_turn` ~1599-1603). Only the primary `_handle_one_turn` arm gets
  the WARNING this slice; the death-record fields still populate via
  `_tombstone_teammate` regardless of which arm tripped.
- A new redaction pattern/version bump — reuse `redact_output` / V1 as-is.

## Assumptions

- **Ring size = last 50 lines, byte cap = 64 KiB** — *Default:* `_STDERR_RING_MAXLEN=50`, `_STDERR_RING_BYTE_CAP=65536`. — *Rationale:* "~50 lines" is the idea's stated target; 64 KiB is double `redact_output`'s 32 KiB persistence cap, giving headroom before the redacted tail is itself capped, while keeping per-teammate memory trivially bounded.
- **Persist via `redact_output` (not `redact_error`)** — *Default:* `redact_output` (32 KiB cap, V1 + output-only patterns). — *Rationale:* `redact_error` caps at 256 bytes — far too small for a ~50-line tail; `redact_output` is the established redactor for multi-line output bodies.
- **New fields live on the existing dead-teammate status payload** — *Default:* add `stderr_tail_at_death` and `in_flight_tools_at_death` keys to the dead branch of the broker's per-teammate status dict (~927-958). — *Rationale:* that dict is already what the dashboard and transcript consume for dead teammates; additive keys require no consumer changes.
- **`in_flight_tools_at_death` reuses `current_tools` shape** — *Default:* a list of the same dicts `status_snapshot()` already emits (`tool_name`, `tool_use_id`, `started_at_wallclock`, `args_summary`), captured before `_close_open_tools`. — *Rationale:* avoids a parallel tracker and reuses the already-redacted `args_summary`.
- **Synthetic exception for AT#7** — *Default:* the unit test defines a local class named `ProcessError` with an `exit_code` attribute rather than importing the SDK's. — *Rationale:* the death-site arm matches by class **name** (`type(exc).__name__`), so a synthetic same-named class exercises the real branch without depending on SDK internals.

## Open Questions

- (none)

## Validation

End-to-end exercise of the promised outcome — "the next death is
self-explaining." Automatable portion: run the full suite (proves the ring
buffer is bounded+redacted, the death record is populated, and the death-site
WARNING fires) — this is the coordinator's post-feature-review gate:

```bash
uv run pytest
```

Manual confirmation of the user-visible outcome (operator-judged, optional):
with `CLAUDE_CREW_LIVE_TESTS=1`, run `uv run pytest tests/test_live_stderr.py`
and confirm the live teammate's `status_snapshot()["stderr_tail"]` is a
populated string after an ordinary turn — i.e. real CLI stderr was captured
through the registered callback without forcing a crash. Pass criteria: the
gated test passes (ring non-empty or `stderr_tail` is a string); fail criteria:
the ring stays empty, indicating the callback was not wired.

## Task Breakout

```yaml
tasks:
  - name: stderr-ring-buffer
    description: |
      Add the bounded, byte-capped stderr ring buffer to SdkTeammate in
      claude_crew/sdk_teammate.py: module constants _STDERR_RING_MAXLEN=50 and
      _STDERR_RING_BYTE_CAP=65536; __init__ fields _stderr_ring (deque) and
      _stderr_ring_bytes; the never-raising _on_stderr_line callback; the
      _stderr_tail_redacted() helper (joins ring, runs redact_output, returns
      None when empty); register opts_kwargs["stderr"] = self._on_stderr_line in
      _run; and add snap["stderr_tail"] + snap["in_flight_tools"] to
      status_snapshot(). Authors the ring-buffer unit tests in
      tests/test_sdk_teammate.py (ATs 1-4).
    dependsOn: []
    acceptanceTests: [1, 2, 3, 4]
    taskTouches: ["claude_crew/sdk_teammate.py", "tests/test_sdk_teammate.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_sdk_teammate.py
  - name: death-record-fields
    description: |
      In claude_crew/broker.py add two TeammateInfo death-record fields
      (stderr_tail_at_death: str|None, in_flight_tools_at_death: list|None),
      populate them in _tombstone_teammate step 4 from the teammate snapshot
      (snap["stderr_tail"], snap["in_flight_tools"]) with None defaults in the
      except-AttributeError and teammate-is-None branches, thread them through
      the dataclasses.replace at step 5, and serialize both keys onto the
      dead-teammate status payload (~927-958). Authors the broker death-record
      tests in tests/test_broker.py (ATs 5-6). Depends on stderr-ring-buffer for
      the snapshot keys it consumes.
    dependsOn: [stderr-ring-buffer]
    acceptanceTests: [5, 6]
    taskTouches: ["claude_crew/broker.py", "tests/test_broker.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_broker.py
  - name: death-site-warning
    description: |
      In claude_crew/sdk_teammate.py _handle_one_turn, augment the existing
      ProcessError/CLIConnectionError/BrokenPipe catch arm (~1479-1491) to emit
      a logging.WARNING carrying exc class name, exit_code (getattr(exc,
      "exit_code", None)), self._last_tool_completed, and
      self._stderr_tail_redacted() BEFORE the unchanged
      _death_suspected/_death_in_flight_envelope/return control flow. Authors the
      death-site WARNING test in tests/test_sdk_teammate.py (AT 7). Depends on
      stderr-ring-buffer (consumes _stderr_tail_redacted; same file — serialized
      to avoid collision).
    dependsOn: [stderr-ring-buffer]
    acceptanceTests: [7]
    taskTouches: ["claude_crew/sdk_teammate.py", "tests/test_sdk_teammate.py"]
    implementationKind: behavior-change
    testCommand: |
      uv run pytest tests/test_sdk_teammate.py -k "stderr or death"
  - name: live-stderr-wiring-test
    description: |
      Add a gated live SDK test (skipped unless CLAUDE_CREW_LIVE_TESTS=1) in
      tests/test_live_stderr.py that spawns a real SdkTeammate via the live
      harness, runs one ordinary completing turn, and asserts the stderr ring is
      non-empty OR status_snapshot()["stderr_tail"] is a string — proving the
      callback is wired end-to-end without forcing a crash. Honors live-test
      conventions: bounded async-iterator drains and HOME-monkeypatch SDK-auth
      preservation (_preserve_sdk_auth). Depends on stderr-ring-buffer for the
      registration under test.
    dependsOn: [stderr-ring-buffer]
    acceptanceTests: [8]
    taskTouches: ["tests/test_live_stderr.py"]
    implementationKind: behavior-change
    testCommand: |
      CLAUDE_CREW_LIVE_TESTS=1 uv run pytest tests/test_live_stderr.py
```

## Design Notes

- The two `sdk_teammate.py` tasks (`stderr-ring-buffer`, `death-site-warning`)
  share the file and the test file; they are serialized by the `dependsOn` edge
  so they never write concurrently. `stderr-ring-buffer` creates the new ring
  fields and helpers and the ring-buffer tests (ATs 1-4); `death-site-warning`
  appends only the WARNING at the catch arm and the AT#7 test — no overlap in
  the regions each edits.
- `redact_output` is imported in `sdk_teammate.py` already (used by the F8 hook
  path); no new import needed for `_stderr_tail_redacted`. `collections` is also
  already imported at module top.
- The death-site WARNING uses `%s`-style lazy logging args (not f-strings) to
  match the module's existing `logger.warning(...)` call style and avoid
  formatting cost when the level is suppressed.
- Per CLAUDE.md, the suite-level gate is the **full** `uv run pytest` because
  this touches widely-consumed `broker`/`SdkTeammate` behavior; per-task
  `testCommand`s are scoped narrower for the implementor's inner loop only.
