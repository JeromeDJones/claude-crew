# Debrief: planner / PASS

The shape of this slice — two truly localized gaps in a freshly-shipped feature — is the cleanest case for RepoReactor and yet still cost three breakout-review cycles.

Worth remembering: when a gap is "completing-task owns the observable," resist the urge to split scaffolding from completion into separate tasks until the schema actually supports it. The cycle-1 instinct to split helper-wiring from AT8-refactor was structurally correct (single-responsibility, separable diffs) but mechanically forbidden by the Invariant-2 / enum mismatch — merging back lost nothing because both observables ship in one file anyway.

Second: when extending a glob-based loader, the cross-format collision rule is the easy-to-miss edge case, and naming it explicitly in the spec (AT-7) saved an implementor round-trip.

Third: `status_snapshot` as a side-channel for per-test telemetry was the right minimum-diff call — no new SDK plumbing, just read what's already there. The "yield results" reshape was tempting; deferring it was correct.
