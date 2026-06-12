# Slice Review: workflow-shape-composition-m0 task=broker-proposals-topology

## Verdict summary
**PASS.** The slice implements all six broker primitives (ATs 5–7), the long-poll/timeout idiom is correct, the tests are non-vacuous, and non-regression is green per coordinator ground truth. No Critical/High findings.

## Check 1 — Slice adherence (ATs 5–7)

**All required surface present and correct (broker.py diff):**

| Required | Status |
|---|---|
| `register_proposal(shape, adaptation_diff=None) → shape_id` (status `"pending"`, 12-hex id) | ✅ lines 1068–1090 |
| `await_proposal(shape_id, timeout)` — `asyncio.Condition` long-poll, `pending → approved/declined/timed_out` | ✅ acquires `_proposal_condition`, `while status=="pending": await wait()`, `asyncio.timeout` → sets `timed_out` |
| `resolve_proposal(shape_id, decision)` — **approve/decline ONLY, no edited_shape** | ✅ rejects anything but `"approve"`/`"decline"` via `ValueError`; **no edited-shape path** |
| `get_proposal(shape_id) → ShapeProposal \| None` | ✅ |
| `record_topology(topology)` / `get_topologies() → tuple` | ✅ |
| `shape_proposals` + `topologies` as defaulted-empty `BrokerSnapshot` fields | ✅ both default `()`, populated in `snapshot()` via `tuple(self._proposals.values())` / `tuple(self._topologies)` — mirrors the `startup_diagnostics` threading pattern exactly |

**Condition idiom mirrors `_lead_message_condition` correctly.** The `register/await/resolve` split is a sound producer/consumer Condition pattern. Lost-wakeup analysis: `resolve_proposal` assigns `status` before acquiring the lock, but `notify_all()` must acquire `_proposal_condition`, which an active waiter holds until `wait()` releases it — so a notify can never land before the waiter is parked, and a status set that races the waiter's predicate check is caught either by the `while status=="pending"` re-check on wakeup or by the pre-wait check. No lost-wakeup, no spurious-wakeup hazard (loop re-checks predicate). ✅

**Tests are genuinely assertive, not vacuous:**
- `test_await_proposal_unblocks_on_approve/decline` — spawn a resolver task that `sleep(0.05)` then resolves, while the main coroutine blocks in `await_proposal(timeout=5.0)`. The asserted `status == "approved"/"declined"` is only reachable *after* the cross-task `notify_all` wakeup. Real wakeup coverage. ✅
- `test_await_proposal_timeout_sets_timed_out` — `timeout=0.05`, no resolver, asserts `timed_out` both on the return value and via `get_proposal`. Real timeout-branch coverage. ✅
- `test_await_already_resolved_returns_immediately` — covers the pre-resolved fast-path. ✅
- AT7 topology tests assert `edges` triples (incl. non-gated `tee`/`direct` modes) and `slot_to_teammate` map survive into `snapshot().topologies` intact, plus empty-by-default. ✅

## Check 2 — Non-regression
- Coordinator ground truth: slice **18/18 pass exit 0**, prior-task `test_shapes.py` **45/45 pass exit 0**. The 2 `test_shutdown_signals` failures are confirmed pre-existing baseline infra flakes, not this slice.
- Diff is **purely additive** (122 insertions, 0 deletions): two new dataclasses, two new `Broker.__init__` fields, two new defaulted snapshot fields, two new kwargs in the existing `snapshot()` construction, six new methods. No existing signature, return shape, or log line altered → nothing downstream can regress.
- New `from claude_crew.shapes import Shape` introduces no circular import (`shapes.py` does not import `broker`; verified by grep + the green runtime imports). ✅

## Check 3 — Code-quality smoke
Clean, well-documented, consistent with house patterns. Type hints present; error paths (`KeyError` on unknown id, `ValueError` on bad decision) fail loud. No smells.

## Lower-tier findings (do not affect verdict)

- **[Low] `resolve_proposal` has no re-resolution guard.** It will happily move a `timed_out` (or already-`approved`) proposal to a new terminal status — the state machine only enforces the *value* of `decision`, not that the source state is `pending`. ATs don't require the guard and no test exercises a double-resolve, but a `pending`-only precondition would harden the gate against a late approval racing a timeout. Worth a backlog note for the feature-reviewer to weigh against AT4/instantiation flow.
- **[Info] `Topology` is `@dataclass(frozen=True)` but carries a mutable `dict` (`slot_to_teammate`).** The frozen guarantee covers rebinding the field, not mutating the dict in place. The docstring's "frozen by construction" slightly overstates immutability. Harmless for the tests; consider `MappingProxyType` or documenting the caller-owns-no-mutation contract if it matters downstream.
- **[Info / cross-slice — feature-reviewer's call]** `ShapeProposal.status` includes `"instantiated"` and `adaptation_diff` is stored but never consumed in this slice — both are seams for the instantiation/composer slice. Out of scope for this review.

**Verdict:** PASS
