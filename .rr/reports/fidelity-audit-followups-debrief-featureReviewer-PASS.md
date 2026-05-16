# Debrief: featureReviewer / PASS

What's worth carrying forward: the spec-quality bar is internalized but the breakout-shape bar isn't — invariants encoded in the breakout-review validator (Invariant 2, the `implementationKind` enum, AT-ownership uniqueness) need to be mirrored as a pre-flight checklist in the breakouter prompt, or they keep being rediscovered through failed cycles.

Second, per-task implementor debriefs carrying concrete pointers forward (field-name locations, retry patterns) measurably improved downstream quality on a 3-task slice — worth treating as a first-class artifact, not an afterthought.

Third, divergent call-site shapes (here: two `TestHookFiringFidelity` classes structurally couldn't use the cost helper because they bypass the broker) only surfaced at slice-review; a "Call-site survey" sub-section in the planner template would force that question upstream.

Finally, pre-existing style smells in touched functions kept getting deferred with the forbidden "pre-existing" label — naming known smells in the implementor prompt is the cheapest closer.
