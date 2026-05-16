## Task

Decompose the approved spec at `/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md` into a task DAG. Write the breakout
artifact to:
`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/breakouts/fidelity-audit-followups.md`

The breakout must conform to the schema in
`doc/templates/breakout-template.md` (read it once before writing — it lives
in the plugin install alongside this prompt). Use the `breakout-feature` skill
for guidance.

## Spec Under Decomposition

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups/.rr/specs/fidelity-audit-followups.md`

Read it. The decomposition you produce must cover every Acceptance Test in
the spec. Reference acceptance tests by their 1-based index in the spec's
`## Acceptance Tests` section.

## Cycle

Cycle: 0
Prior breakout-review report (empty on cycle 0): 

On cycle ≥ 1, read the prior breakout-review report first. Address every
Critical and High finding in the revised breakout. Medium and Low findings
are advisory.

## Working Directory

`/home/jerome/dev/claude-crew/.rr-worktrees/fidelity-audit-followups`

Change to this directory before all file operations.

## Instructions

- The breakout must include `## Goal`, `## Tasks`, and `## Risks` sections.
- `## Tasks` must contain a fenced ```yaml block with a top-level `tasks:`
  list. Each task entry needs `name`, `description`, optional `dependsOn`,
  optional `acceptanceTests`.
- Decompose into the smallest set of tasks that covers the spec without
  introducing artificial dependencies. Two tasks that touch the same files
  but produce independent observable outcomes is fine. One mega-task
  covering the whole spec defeats the purpose.
- Every spec acceptance test must be claimed by exactly one task via
  `acceptanceTests:`. A test claimed by zero tasks is scope leakage; a test
  claimed by two tasks is a duplication risk.
- `dependsOn` edges express *required* ordering — task B genuinely cannot
  start before A finishes. Spurious edges block parallelism (M3) and bloat
  the critical path. If two tasks could run in either order, declare them
  with no edge between them.
- The dependency graph must be acyclic. The schema check rejects cycles.
- Write the breakout file only. Do not implement any code.
