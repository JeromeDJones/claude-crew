# Workflow Retro: fidelity-audit-followups

**Mode:** opt-in (workflowRetroEnabled set true after validation PASS via user choice; default-off otherwise)
**Slug:** fidelity-audit-followups
**Date:** 2026-05-16

---

## Coordinator Observations

Frictions I encountered driving this slice:

- **3 breakout-review cycles before PASS.** Cycle 0: AT-1 ownership drift between two tasks. Cycle 1: Invariant 2 violation (helper-only task with empty `acceptanceTests`). Cycle 2: schema enum mismatch (`implementationKind: scaffolding` proposed by me as coordinator, rejected by `breakout-schema-check.sh`). The schema validator enforces invariants; the breakout-feature template does not surface them.
- **Coordinator wrong-advice.** I suggested `implementationKind: scaffolding` as a fix at cycle 1. That value isn't in the closed enum (`documentation` | `behavior-change`). Cost: one extra breakout cycle. The schema check caught it; my prompt context did not.
- **`rr-slice-reviewer` died twice mid-slice** with `model returned no text content` (~955k input tokens, then ~510k after respawn). Task-2's slice-review report was coordinator-authored. Already routed to `~/dev/FDE/doc/BACKLOG.md d011586` (per-task slice-reviewer + Haiku-delegated reads).
- **`.rr/` gitignore conflict.** Every PASS commit required `git add -f .rr/` (this repo gitignores `.rr/`). Already routed to FDE BACKLOG from #27 dogfood.
- **`state-op.sh` lacks a per-task implementor map setter.** Coordinator wrote `state.teammates.implementor.<task-name>` directly via `jq` for every per-task spawn + cleanup. Already routed to FDE BACKLOG from #27.
- **Architecture-conjunct-check.sh false-positives on multi-line bullet prose** in the spec's Architecture Overview. The check splits bullet bodies into fragments and grep-checks each against task `description` text — backtick-laden bullets get mis-matched. Reviewer overrode at cycle 0 + 1; no real coverage gap.
- **Cycle-0 hex-relay flake → coordinator manual retry.** Task 2's validation cycle 0 hit a known LLM hex-relay truncation (`...40ea1a` vs `...40ea1aa`). I made a judgment call to retry once before escalating; no automation for "known flake, retry once before user-surface".

---

## Role Reflections

### Planner (rr-planner, t-f2b31aaa6691)

> From the planner seat the sharpest friction is that `breakout-template.md` documents `implementationKind` as a closed enum (`documentation` | `behavior-change`) while the *Invariants* the breakout-reviewer enforces (every `behavior-change` task must claim ≥1 AT; helper/scaffolding tasks have nowhere to live) push naturally toward a third value. Three review cycles burned because the template's enum and the reviewer's invariants don't compose: any task whose observable is completed by a successor either co-claims an AT (duplication risk) or declares `acceptanceTests: []` and gets flagged Invariant-2. The skill needs to pick a side — either add `scaffolding` (or `helper`) to the enum and let the reviewer accept it as AT-exempt, or document explicitly in `breakout-feature` SKILL.md that "completing-task owns the AT, predecessors merge into it." The current template is silent on both, so the planner re-invents the resolution per slice. Smaller win: the conjunct-check passing literally vs. via reviewer override would benefit from an explicit `### Architecture conjunct → task mapping` subsection in the breakout template itself — I added one freehand this cycle, but baking it into the template means every planner emits it and reviewers don't have to reason about prose bullets.

### Plan-reviewer / breakout-reviewer (rr-plan-reviewer, t-3e74040c7bd1)

> The highest-leverage fix from my vantage point is making the **task-shape invariants visible at authoring time, not review time**. Three breakout-review cycles happened because Invariant 2 (`implementationKind: behavior-change` requires at least one claimed integration AT) and the AT-isolation property (a task's claimed ATs must be *verifiable by the slice reviewer in isolation*, not just eventually true at the terminal gate) aren't surfaced to the planner when it writes the breakout — they're only caught when I read the artifact afterward. The planner had to discover through two failed reviews that the helper-only task shape is structurally incoherent under the schema's legal `implementationKind` values. A pre-review schema check that flags `behavior-change` + empty `acceptanceTests` before routing to me, combined with a one-paragraph "AT isolation" rule in the breakout template (not just `review-breakout`), would have collapsed three cycles to one. The template I did see told me *what to check*; the planner's template didn't tell it *what to avoid*. That asymmetry is the core friction — the critic knows the invariants cold, but the author is learning them through rejection.

### Feature-reviewer (rr-feature-reviewer, t-6a7607c57de7)

> The breakout schema is the leverage point I felt most. The planner shipped a clean spec on the first cycle; the breakouter took three. The asymmetry tells me the spec-quality bar is internalized but the DAG-shaping invariants (Invariant 2, the `implementationKind` enum, AT ownership uniqueness) are not — they live in the schema validator, not in the breakouter's prompt or template. The breakouter is rediscovering them by failing. Cheapest fix: a 5-bullet "schema invariants" checklist in the breakouter prompt or as the first H2 of the breakout template, citing the enum values verbatim. As a downstream reviewer I also noticed the spec's "one helper" framing for cost-capture obscured that two classes structurally cannot use the helper (`ClaudeSDKClient` direct, no broker handle) — the planner template doesn't prompt for "list call-sites and their differences", so divergent shapes only surface at slice-review. A "Call-site survey" sub-section under Architecture Overview would force that question. Finally: feature-review and slice-review keep re-flagging the same Mediums (style smells in touched files, double-I/O in symmetric branches) — these are exactly the things an implementor-prompt pre-flight checklist could surface before code lands, not after. The pattern is consistent: invariants encoded in reviewers should be mirrored as checklists in the upstream prompt.

---

## User Input

_Pending — coordinator surfaces draft to user; user reply will land here._
