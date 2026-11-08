---
status: accepted
date: 2026-11-07
---

# Own Savant CSV fetcher instead of pybaseball

Bronze must preserve exactly what Savant sent. pybaseball returns a type-inferred
DataFrame, has lagged on maintenance, and does not validate responses: during the
cross-check it crashed on a non-CSV body that Savant returned with HTTP 200. Both depend on
the same undocumented `statcast_search/csv` endpoint, so writing our own (one request per
game date, validation, retries, rate limiting) adds no endpoint risk and gives us control
over failure handling. pybaseball remains available in the optional `crosscheck` dependency
group for spot checks.
