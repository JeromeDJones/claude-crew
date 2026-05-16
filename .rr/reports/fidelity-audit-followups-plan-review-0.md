# Plan Review: fidelity-audit-followups (cycle 0)

**Spec:** /home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md
**Cycle:** 0
**Reviewed:** 2026-05-16
**Verdict:** PASS

## Summary

Reviewed the `fidelity-audit-followups` spec closing two semantic gaps in the #27 fidelity-audit suite: a cost-telemetry fixture that writes all-zeros, and a YAML-loader bypass that lets AT8 pass without the loader being exercised. The spec is detailed, well-scoped, and produces concrete observable outcomes for all 8 acceptance tests. One file-name discrepancy between the Problem section and the Architecture/Data sections (naming `_loader.py` where `_user_loader.py` is consistently used elsewhere) is the primary quality concern; it is resolvable via code search and does not block implementation. No Critical or High findings.

## Findings

### Critical

_None identified._

### High

_None identified._

### Medium

- [MEDIUM-01] `spec.problem.term-drift` — Problem: The `yaml-loader-bypass` root cause is attributed to `claude_crew/subagents/_loader.py::discover_dir`, but both the Architecture Overview and Data/API Contracts sections consistently name `claude_crew/subagents/_user_loader.py::discover_dir` as the change target. An autonomous implementor reading Problem first would open the wrong file; they'd have to grep the source to resolve the contradiction. resolve-contradiction.

- [MEDIUM-02] `spec.acceptance-tests.vague` — Acceptance Tests / AT-5(c): Asserts "both sentinels appear in the parent reply under live mode" without defining what "sentinel" means anywhere in the spec. The term is borrowed from the existing test's fixture vocabulary without explanation. An autonomous implementor writing the refactored assertion must inspect the existing `TestAgentFormatYamlPolymorphism` to discover the observable, introducing an implicit external dependency. clarify-scope.

- [MEDIUM-03] `spec.acceptance-tests.vague` — Acceptance Tests / AT-6: Opens with "byte-for-byte equivalent to the pre-change behavior" then qualifies to "(same keys, same `AgentDefinition` fields)." Byte-level serialization equality and Python object field equality are distinct assertions with different sensitivity; the leading phrase misleads. An implementor may implement a stricter assertion than intended (e.g., comparing pickled bytes) or debate which framing governs. clarify-scope.

### Low

- [LOW-01] `spec.cross-section.ambiguous` — Acceptance Tests / AT-3, AT-4, AT-7: These scenarios cover YAML loader behavior but the AT section does not state which test file they belong to. Design Notes separately names `tests/test_user_loader.py` as the canonical home. Without the linkage in the AT section itself, an implementor may place new tests in `tests/test_fidelity_audit.py`, which is already the dominant file in scope. clarify-scope.

- [LOW-02] `spec.data-contracts.ambiguous` — Data/API Contracts: `parse_yaml_pack_file` is annotated as "Loader extension in `claude_crew/subagents/_loader.py`" while its sole caller (`discover_dir`) is in `_user_loader.py`. Whether the new helper co-locates with its caller or in `_loader.py` as a shared primitive is left implicit; the spec says "the spec leaves the function shape to the implementor" but does not extend that latitude to file placement. clarify-scope.

## Persistent Findings

_None — first cycle or no recurrence._

## Spec Quality

- **Acceptance tests:** Clear — 8 numbered scenarios, all with measurable outcomes; minor vagueness in AT-5(c) and AT-6 framing.
- **Test command:** Present and runnable — default-CI and full-live variants both syntactically correct; prerequisites named; live spend estimated.
- **Scope boundary:** Clear — Out of Scope entries are non-trivial and coherent with the Problem and Acceptance Tests.

## Verdict Rule Applied

PASS: no Critical or High findings.
