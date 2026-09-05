# OD-CPD MCP — project guide for Codex

NYC Capital Projects data (4 Socrata datasets) served over a local DuckDB as an
MCP server.

## Read this first

- **`docs/FEATURES.md` is the canonical inventory** of the MCP's tools and the
  domain rules it encodes — PID↔FMS many-to-many, the sponsor-driven category
  taxonomy, signed-value reporting, reporting cadence, the `(managing_agency, fms_id)`
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

## Adding or changing domain knowledge

Treat a domain rule as a tested data contract. A repository instruction alone does
not reach agents that connect only through MCP.

1. **Define the rule and its consequence.** Identify the entity/key, reporting
   period or population, exceptions, and what the caller must calculate or say.
   Keep changing counts and percentages in dated data responses or snapshot tests,
   rather than presenting them as timeless instructions.
2. **Use the shared rule registry.** Add or update a stable ID in
   `src/od_cpd/primer.py: RULES` and assign it to relevant tools in `TOOL_RULE_IDS`.
   The registry generates server instructions, `dataset_info.domain_rules`, scoped
   tool descriptions, and typed `interpretation_rules` in MCP success results.
   Register tools through the server's `_domain_tool` decorator. Do not assume the
   client forwards server instructions or calls `dataset_info` first; keep each
   tool's guidance sufficient for a direct call and avoid copying the entire primer.
3. **Enforce what can be calculated.** Put keying, deduplication, validation,
   classification and scope selection in the appropriate tools or curated tables.
   Raw SQL's read-only guard does not validate analytical correctness. Use YAML for
   dictionary/taxonomy changes and verify the grain of any affected aggregation.
4. **Return evidence with the answer.** Expose all linked entities, the complete
   budget-line key, period/population, metric basis, direction, exclusions and
   truncation as applicable. Derive caveats from observed facts. A single forward
   link never proves a reciprocal 1:1 relationship; attributed funding is not an
   allocated share of a shared budget.
5. **Test the failure the rule prevents.** Add deterministic synthetic cases that
   fail when the rule is ignored. Relationship changes cover both directions,
   shared funding, no counterpart, and same-ID/different-holder lines as applicable.
   Preserve existing behavioral tests and add focused cases for exceptions.
6. **Verify actual MCP delivery.** Check `initialize.instructions`, published
   `tools/list` descriptions and output schemas, and `tools/call` structured/text
   parity. Verify a direct data call carries its applicable rules even before
   `dataset_info`. Registry coverage must include every tool and every rule.
   Distinguish these deterministic checks from fresh-context agent evaluations of
   final answers; explicitly report when model-level evaluations were not run.
7. **Document and activate.** Update `docs/FEATURES.md` and its date in the same
   change. Verify the affected behavior and protocol contracts. Reconnect stdio
   clients to load code changes; rematerialize only when stored data logic changed,
   using the documented atomic publication workflow.
