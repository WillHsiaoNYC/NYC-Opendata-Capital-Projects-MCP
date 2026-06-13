# OD-CPD MCP — project guide for Claude

NYC Capital Projects data (4 Socrata datasets) served over a local DuckDB as an
MCP server.

## Read this first
- **`docs/FEATURES.md` is the canonical inventory** of the MCP's tools and the
  domain rules it encodes — PID↔FMS many-to-many, the sponsor-driven category
  taxonomy, signed-value reporting, reporting cadence, the (managing_agency, fms_id)
  budget grain, etc. Read it before answering data questions or changing behavior.
- **Keep `docs/FEATURES.md` current.** Whenever the MCP gains a tool, a built-in
  domain rule, or a taxonomy/behavior change, update the relevant section and bump
  its "Last updated" date — ideally in the same PR.
- **Every aggregation in `materialize.py` encodes a keying assumption** — which
  entity (budget line, PID, pair, snapshot) each published column attaches to.
  Verify the keying before changing it.

## Running & testing
- Tests: `uv run pytest` (fallback: `PYTHONPATH=src python -m pytest`).
- The MCP server is **stdio**, launched by the client as
  `uv run --directory <repo> od-cpd-server` with `PYTHONPATH=<repo>/src`. Bare
  `uv run od-cpd-server` can fail with `ModuleNotFoundError` when the editable
  `.pth` is missing or hidden (e.g. under iCloud), so always set `PYTHONPATH=src`.
- **Code changes do not reach the running server until it is reconnected** — it's a
  stdio subprocess. Reconnect via `/mcp` (or restart the client) to load new code.

## Updating the live database
- `od-cpd init` / `od-cpd update` re-download all four datasets from Socrata (full ingest).
- To apply YAML / `materialize.py` changes **without re-downloading**, re-materialize the
  existing raw tables via the atomic-swap pattern: copy `var/cpd.duckdb` → a shadow file,
  open it read-write, run `materialize.materialize_all(con)`, then
  `ingest.atomic_swap(shadow, db)`. Never open the live DB read-write directly while the
  MCP may touch it — the shadow + atomic swap keeps the running server safe.

## Architecture orientation
- **Curated dictionaries drive classification — edit YAML, not Python:**
  `data/agencies.yaml` → `agency_dim`, `data/categories.yaml` → `category_dim`.
- `src/od_cpd/materialize.py` builds the normalized + analytics tables and the category
  dimension; `src/od_cpd/categories.py` compiles `categories.yaml` into the `category_dim`
  CASE expression.
- **Category taxonomy:** 3-tier precedence — specific ten-year keyword / fms-id prefix →
  sponsor routing → generic facility keyword → `Other`. **File order in `categories.yaml`
  is precedence** among tier-1 keyword matches. Institution categories (Library, Cultural)
  are owner-authoritative via `ever_managed_by` (all-history; survives reassignment).
- **Classify by the stable signal** — fms-id/budget-line prefix, `sponsor_agency`, or the
  `ten_year_plan_category` label — **never project name**, which reassigns and undercounts.
  `managing_agency` is the *builder/budget-holder*, not the owner: use it only for the three
  construction-manager agencies (DDC/DCAS/EDC) whose work IS what they manage. For everyone
  else, "their projects" = `sponsor_agency`. This is the role-aware rule baked into the
  agency-scoped tools (`agency` + `agency_role`); see `docs/FEATURES.md` §4.

## Workflow
- Tests must pass before landing; one PR per change.
