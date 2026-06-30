# Build Report — proposal-payload-structured-shape — cycle 0

## Verdict

**PASS**

## Task

`proposal-payload-structured-shape` (index 0) of the `shape-graphic-redesign` breakout.

Acceptance tests owned: AT 9, AT 10.

## Test Command

```
uv run pytest tests/test_shape_proposal_payload.py
```

## Test Results

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.3, pluggy-1.6.0
collected 3 items

tests/test_shape_proposal_payload.py::TestProposalPayloadStructuredShape::test_proposal_carries_mermaid_and_shape PASSED [ 33%]
tests/test_shape_proposal_payload.py::TestProposalPayloadStructuredShape::test_existing_keys_preserved PASSED [ 66%]
tests/test_shape_proposal_payload.py::TestShapeToDictWiredInUiServer::test_shape_to_dict_referenced_in_ui_server PASSED [100%]

============================== 3 passed in 0.44s ===============================
```

Exit code: **0**

## Files Changed

```
M  claude_crew/ui_server.py
?? tests/test_shape_proposal_payload.py
```

(from `git diff --name-status HEAD` + `git status --short`)

## Implementation Summary

### `claude_crew/ui_server.py`

Two additive changes:

1. **Import**: Added `shape_to_dict` to the existing import from `claude_crew.shapes`
   (line 34: `from claude_crew.shapes import shape_to_dict, shape_to_mermaid`).

2. **Serialization**: Added `"shape": shape_to_dict(p.shape)` as a new key in the
   `shape_proposals` list comprehension inside `_build_local_instance`. The existing
   keys (`shape_id`, `crew_id`, `status`, `adaptation_diff`, `mermaid`, `name`,
   `summary`) are fully preserved — this is a pure additive change.

### `tests/test_shape_proposal_payload.py` (new file)

Two test classes:

- **`TestProposalPayloadStructuredShape`** (AT 9):
  - Builds a `Shape` with two `ShapeNode`s and one `ShapeEdge(mode="gated")`.
  - Constructs a `ShapeProposal` (status="pending") and wraps it in a `BrokerSnapshot`.
  - Calls `UIServer._build_local_instance(snap)` and asserts:
    - `shape_proposals[0]` contains both `"mermaid"` and `"shape"` keys.
    - `shape["nodes"]` is non-empty.
    - `shape["edges"][0]["mode"] == "gated"`.
  - Second test case asserts all original keys are still present (additive contract).

- **`TestShapeToDictWiredInUiServer`** (AT 10):
  - Reads `claude_crew/ui_server.py` source and asserts the string `shape_to_dict`
    appears in it (structural deletion-detector).

## Scope Creep

None. Only the two files owned by this task were modified.

## Remaining Failures

None — 3/3 tests pass; exit code 0.
