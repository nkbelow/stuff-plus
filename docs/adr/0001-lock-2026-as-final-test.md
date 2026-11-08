---
status: accepted
date: 2026-11-07
---

# Lock 2026 as the final test; train on 2022–23, tune on 2024

The model needs two kinds of test: pitch-level accuracy on unseen pitches, and whether a
pitcher's season-N scores predict season N+1. The original 2023/24/25 split could only
provide the second by reusing the test season, and 2026 is now a complete season. So we
train on 2022–23, tune and early-stop on 2024, use 2025 only as the development "next
season" outcomes for 2024 scores, and run the final test once: retrain on 2022–24, score
2025, evaluate against 2026. We exclude 2020 (60 games), 2021 (the mid-June sticky-substance
crackdown shifted spin mid-season) and pre-2020 (TrackMan, not Hawk-Eye).

## Consequences

- 2026 is spent the moment any metric is computed on it, so only the final experiment
  config may touch it. Its config and acceptance thresholds are committed before it runs.
- If exploration shows a 2022→2023 shift in pitch physics, fall back to training on 2023
  only; the rest of the protocol is unchanged.
- 2026 introduced ABS challenges and measures `plate_x/z` mid-plate, so the final test is
  slightly harder than development. Location is not a feature, which limits the impact.
