# Debrief: implementor / task live-test-cost-and-yaml-dispatch / PASS

The hook tests (TestHookFiringFidelity) were the surprise — the task description listed them in the "call the helper from all 7" list, but they use ClaudeSDKClient directly rather than the broker, so there's no tid to pass to `_record_sdk_cost`. The spec's architecture note ("each test spawns a fresh SdkTeammate via sdk_factory") was slightly over-stated. The fix was straightforward: capture the ResultMessage from the existing drain loop and extract `.usage` / `.total_cost_usd` inline.

**Lesson for full-validation-baseline**: the hook tests may skip (HookMatcher gate) under some SDK builds — if live validation returns fewer than 10 executed tests, check whether both hook methods skipped before concluding a cost-line gap is a regression; the AT-1 "≥7 non-zero" threshold was sized to absorb exactly this.

**Process note**: the breakout's `taskTouches` list for this task (`tests/test_fidelity_audit.py` only) was accurate and helped — single-file scope made the impl fast. Future breakouts should call out when a task's "7 classes" includes classes that don't use the broker, since the helper signature differs.
