# Snapshot golden evaluations

Deterministic regression goldens distilled from real analytical questions:
each test
replays a real analytical question through the tool functions against the LIVE
DuckDB and asserts both the headline numbers AND the rule-conveyance artifacts
(M:M list-all caveats, signed envelopes, agency_scope echoes, in-band notes).

- **Pinned periods:** January (`202601`) and May (`202605`) 2026 each have a
  module. A module skips unless the selected database's latest source period
  matches its snapshot. Set `OD_CPD_DB` to test a retained snapshot. These are
  period-specific facts; the deterministic synthetic tests cover domain invariants
  without any database download.
- **Refresh review:** May checks compare tool totals with independently queried
  raw rows, explicit current/latest-known populations, schedule-source coverage,
  invalid durations and owner/category cases. The complete budget inventory grew
  from 5,529 lines / $157,986,963,405.09 in January to 5,647 lines /
  $160,336,058,990.19 in May. Older pins remain intact. For a future refresh, inspect
  the retained before/after health report, independently reconcile changed figures,
  and add reviewed pins for the new snapshot.
- **Tier 2 (deferred):** end-to-end LLM evals — an agent answers the same
  questions through the MCP and is judged on rule conveyance (lists all
  counterparts, states the variance basis, scopes DOC by sponsor). Needs a
  model/budget/cadence decision.

Run: `uv run --locked pytest -ra tests/evals/` (included in the default suite).
The skip summary makes missing or mismatched snapshot coverage visible. PR CI
always runs synthetic and real-stdio contract checks, even without a local DB.
