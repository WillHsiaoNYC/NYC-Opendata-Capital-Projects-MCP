# Golden evals (gap 4, tier 1)

Deterministic regression goldens distilled from real analytical questions:
each test
replays a real analytical question through the tool functions against the LIVE
DuckDB and asserts both the headline numbers AND the rule-conveyance artifacts
(M:M list-all caveats, signed envelopes, agency_scope echoes, in-band notes).

- **Pinned period:** the whole module SKIPS unless `var/cpd.duckdb`'s latest
  reporting period equals `GOLDEN_PERIOD` — goldens are facts about one
  snapshot, not invariants. After the next ingest, re-pin: bump
  `GOLDEN_PERIOD` and re-derive the numbers from the live tools.
- **Tier 2 (deferred):** end-to-end LLM evals — an agent answers the same
  questions through the MCP and is judged on rule conveyance (lists all
  counterparts, states the variance basis, scopes DOC by sponsor). Needs a
  model/budget/cadence decision.

Run: `uv run pytest tests/evals/` (included in the default suite; skips cleanly
when the DB is absent or re-ingested onto a newer period).
