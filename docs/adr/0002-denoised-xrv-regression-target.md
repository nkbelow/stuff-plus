---
status: accepted
date: 2026-11-07
---

# Regress on denoised xRV, not raw `delta_run_exp`

Raw `delta_run_exp` mixes pitch quality with base-out state, defense, park and batted-ball
luck, and it is heavy-tailed enough that squared loss is dominated by home runs. The v1
label replaces each pitch's actual result with the training-season average run value of its
outcome class (ball, called strike, whiff, foul, HBP) or, for balls in play, of its
exit-velocity × launch-angle bucket. One LightGBM regressor with squared loss is fit on that.
In expectation this estimates the same quantity as a classify-then-weight model
(Σ P(outcome | physics) × value), with one model instead of four.

## Considered options

- **Raw `delta_run_exp` regression**: rejected; too noisy and biased by context.
- **Decomposed classification** (P(swing), P(whiff | swing), P(foul | contact),
  E[value | in play]): more diagnostic, but four models plus calibration. Planned as v2,
  compared against v1 on the same evaluation harness.
- **Integrating out location** (train with location and count, score over a reference
  distribution): the most faithful to "stuff", but the most work. A stretch experiment.
