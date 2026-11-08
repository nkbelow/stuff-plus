---
status: accepted
date: 2026-11-07
---

# Bronze completion markers are per-partition `_meta.json` files, not a manifest table

A central manifest file would need a read-modify-write for every date, and after a crash it
could disagree with the data on disk. Instead each date partition writes its data file, then
`_meta.json` last, both via atomic rename. The presence of `_meta.json` means the partition is
complete, and the manifest is a DuckDB view over all sidecars. Bronze stores only the raw
`.csv.gz`; there is no Parquet copy, because DuckDB reads gzipped CSV directly and a second
copy could drift from the source bytes.
