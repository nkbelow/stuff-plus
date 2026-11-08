---
status: accepted
date: 2026-11-07
---

# Silver keeps Savant's column names, units and full column set

The original plan converted movement from feet to inches and renamed columns in silver.
Instead, silver is a full-fidelity typed copy of Savant's export: every non-deprecated
column (~112), with Savant's names and units unchanged and units recorded in the schema
registry. A `pfx_x` in silver means exactly what the Savant CSV docs say, so silver can be
checked against the source column by column. All interpretation (unit conversion, handedness
mirroring, outcome classes, filters) happens in gold, where derived columns carry their unit
in the name (`hb_arm_in`).

## Consequences

- Gold must convert units explicitly; a raw `pfx_x` used as inches would be off by 12×.
  CODING_STANDARDS.md §1.8 states the naming rule that makes this visible.
- Silver is wider than any model needs. Adding a column to a later feature set never
  requires rebuilding silver.
