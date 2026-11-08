# Stuff+ model

A custom Stuff+-style metric: how good a pitch is from its physical characteristics alone,
learned from MLB Statcast data.

## Data layers

**Bronze**:
Statcast data exactly as Baseball Savant sent it, one file per game date.
_Avoid_: raw layer, landing zone

**Silver**:
The cleaned, typed record of every regular-season pitch, with Savant's column names, units and
meanings unchanged.
_Avoid_: clean layer, staging

**Gold**:
Model-ready labels, features and training datasets derived from silver, where every
interpretation (unit conversion, mirroring, outcome classes) happens.
_Avoid_: feature store, mart

**Schema contract**:
The single agreed definition of silver's columns (name, type, unit, group, and the first season
each is expected to be populated) that every silver build is checked against.
_Avoid_: schema registry (that's Kafka's term for a service), data dictionary

## Pitches and outcomes

**Automatic call**:
A ball or strike awarded for a pitch-clock violation, with no pitch thrown; it changes the count
but carries no pitch physics.
_Avoid_: automatic pitch, clock pitch

**xRV**:
A pitch's denoised, context-neutral run value: the training-season average value of its outcome
class, or of its exit-velocity and launch-angle bucket for balls in play.
_Avoid_: run value, delta run expectancy (those are the raw, noisy quantity)

## Evaluation

**Locked test season**:
2026, held out for the single final evaluation; no metric is computed on it before then.
_Avoid_: test set, holdout (both are ambiguous between seasons)
