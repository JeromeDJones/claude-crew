# Build Report — death-site-warning (cycle 0)

## Task

`death-site-warning` (index 2) of the `teammate-death-diagnostics` feature.

Add a `logging.WARNING` at the ProcessError/CLIConnectionError/BrokenPipe catch arm in `_handle_one_turn` carrying `exc_class`, `exit_code`, `last_tool_completed`, and `stderr_tail` — additive only, control flow unchanged. Author AT 7 test.

## Test Command

```
uv run pytest tests/test_sdk_teammate.py -k "stderr or death" --tb=short -q
```

## Result

**PASS** — 8 passed, 116 deselected

## Changes

```
M       claude_crew/sdk_teammate.py
M       tests/test_sdk_teammate.py
```

### claude_crew/sdk_teammate.py

Added `logger.warning(...)` inside the death-match branch of the `except Exception as exc` arm in `_handle_one_turn` (~line 1547), immediately before the unchanged `self._death_in_flight_envelope = env` / `self._death_suspected = True` / `return` sequence:

```python
logger.warning(
    "sdk-death: teammate=%s exc_class=%s exit_code=%s"
    " last_tool=%s stderr_tail=%s",
    self.id, exc_name, getattr(exc, "exit_code", None),
    self._last_tool_completed, self._stderr_tail_redacted(),
)
```

No imports added (all symbols already in scope). No control-flow change.

### tests/test_sdk_teammate.py

Added `TestDeathSiteWarning` class at the end of the file with `test_death_site_warning_emitted` (AT 7):

- Synthetic `ProcessError` exception class with `.exit_code = 1`
- Minimal `_FakeClient` whose `query()` raises the synthetic exception
- Monkeypatches `_stderr_tail_redacted` → `"tail-marker"` and sets `_last_tool_completed`
- Uses `caplog.at_level(logging.WARNING)` to assert all four required substrings appear in the WARNING record
- Also asserts `_death_suspected is True` and `_death_in_flight_envelope is env` to verify control flow is unchanged

## Acceptance Tests

| AT | Description | Status |
|----|-------------|--------|
| 7  | Death-site WARNING contains exc_class, exit_code, last_tool, stderr_tail | ✅ PASS |

(ATs 1-4 from the prior `stderr-ring-buffer` slice also pass — 8 total in the `-k "stderr or death"` filter.)

## Remaining Failures

None.

## Exit Code

0
