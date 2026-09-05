# OD-CPD MCP — Feature & Rules Reference

> **Maintenance:** keep this current. Whenever the MCP gains a tool, a built-in
> domain rule, or a taxonomy/behavior change, update the relevant section below
> and bump the "Last updated" date. This file is the canonical inventory of what
> the server does and the rules it encodes.
>
> _Last updated: 2026-09-05_

The MCP serves NYC Capital Projects data (4 Socrata datasets) over a local DuckDB,
with domain rules baked in so callers don't have to rediscover them.

## 1. Tools (the callable surface)

**Discovery / metadata**
- `dataset_info` — per-dataset freshness, current period, available reporting periods (typed-table basis; qj5n adoption-month original-budget records excluded by construction), row counts, key caveats, per-dataset field definitions, and `domain_rules` (the full primer, embedded so clients that drop MCP server instructions still get the rules on the first orienting call)
- `list_agencies` — agency dictionary (from `agencies.yaml`) + live CPD presence + schedule-executor flag
- `list_categories` — program/facility categories with budget-line counts & totals at the selected complete budget snapshot, using the same fullness resolver as other period aggregates
- `describe_field` — official field definitions (description, allowed values, primary/foreign key, limitations, notes), filterable by field and/or dataset (both filters are case-insensitive SUBSTRING matches)
- `describe_table` — schema catalog for every queryable DuckDB table (typed analytics tables, dims, raw mirrors, `meta`): live columns/types from `information_schema` + curated grain/keying notes from `data/tables.yaml` (drift-guarded against the built DB). No arg → catalog; `table=` (case-insensitive) → full detail; raw tables point to `describe_field` for field semantics

**Resolution & detail**
- `resolve_project_reference` — exact IDs first, then literal partial IDs, historical names and descriptions. Results are ordered deterministically and deduplicated by PID or `(managing_agency, fms_id)`, with each entity's latest nonempty display name. `limit` (1–500, default 50) and `offset` paginate both buckets; `pagination.schedule` / `pagination.budget` disclose total/returned counts, truncation and `next_offset`. `matched_field` and `match_type` identify the match; `%` and `_` match literally
- `get_project_schedule` (PID) / `get_project_budget` (FMS line) — full detail + linked counterparts. Both sides list only counterparts from the anchor's LATEST link period (stale links are not current); FMS ids match case-insensitively. Schedule answers carry `forecast_past_due` (see §5)
- `get_project_history` — period-by-period snapshots for one project (the QA-replay tool). PID lens: phase/forecast/signed variance/reason per period + current-state header with cumulative variance; artifact rows (|variance| > 36,500 days) are kept but flagged `variance_artifact`. FMS lens (case-insensitive): per-line series at the (managing_agency, fms_id) grain — multi-agency ids list ALL lines; the adopted `original_budget` rides a header (adoption months are calendar months, not snapshots; adoption-only lines are a valid header-only answer); the PID lens's `current_state` carries `forecast_past_due` (see §5)

**Schedule analytics**
- `schedule_breakdown` — counts/averages by agency/sponsor/borough/phase/category. An explicit `period` is validated (off-cadence or absent periods error — no silent empty results). For `metric='schedule_variance'`, `statistic` ∈ {count, mean, median, sum, min, max} — anything else errors (no silent fallback); `count` results carry no direction (unsigned). Variance statistics exclude forecast-placeholder artifacts (|variance| > 36,500 days — the guard shared with `rank_projects`) and echo the dropped count as `excluded_artifacts`. Category grouping counts a PID once in EACH of its line-derived categories (non-additive, caveat in-band)
- `schedule_changes` — newly completed / newly delayed between two periods. BOTH change types compare `from_period` → `to_period` ("newly delayed" = positive variance at `to`, none at `from`). Periods are validated: off-cadence, inverted, or missing-`to_period` values error; a `from_period` predating the data is allowed and noted; rows carry `agency_project_name`
- `delay_reason_stats` — distribution of delay reasons; an explicit `period` is validated (off-cadence or absent periods error — no silent empty results), except the `scope='all_history'` path which skips periods; the answer carries `coverage` (delayed_total / with_reason / without_reason) so the distribution has a denominator. Coverage counts delayed rows by bare `variance_day>0` — the same count basis as `schedule_breakdown`'s `count` metric (no artifact-day guard, which applies only to day-valued statistics)
- `project_duration_stats` — forward duration from actual design start to actual construction end, using latest-known PID dates. Reversed or identical milestones error. Negative intervals remain inspectable in `invalid_intervals` and are excluded from statistics. `population_total = n_projects + excluded_missing_dates + excluded_invalid_order`, also within each optional agency/borough/lifecycle group

**Budget analytics**
- `budget_breakdown` — budget/spend by agency or category, deduped on (fms_id, managing_agency); an explicit `period` is validated (off-cadence or absent periods error — no silent empty results); category is line-grain via `category_dim` (one category per line — additive)
- `budget_change` — Δ budget/spend for an agency or FMS line between periods. An FMS id held by several managing agencies is several distinct lines (the (managing_agency, fms_id) grain): the result lists per-line deltas — never a cross-agency sum; optional `managing_agency` scopes to one line

**Portfolio**
- `project_portfolio` — PID-grain cross-section: filter by category ∩ borough ∩ community_board ∩ lifecycle_status ∩ agency(+role), ordered by nearest completion (NULLs last). Borough filters match the line-derived `boroughs` list (multi-borough PIDs found by any borough); the `community_board` filter matches only the PID's CURRENT (latest link-period) funding lines, so a line dropped in an earlier reporting period no longer resurfaces the PID (mirrors the "stale links are not current" rule). Summary covers the FULL filtered set with BOTH budget bases — per-PID `attributed_budget_total` (shared lines count on each PID) and deduped `line_budget_total` (the cash view). Rows carry `forecast_past_due` (see §5). Replaces the recurring "category ∩ borough ∩ status, schedule⨝budget, by completion" `run_sql` pattern

**Ranking & raw**
- `rank_projects` — rank schedules (PIDs) or budgets (FMS lines); supports a `category=` filter. `min_total_budget` / `max_total_budget` apply on both entities (schedule: `attributed_budget`; budget: `latest_budget`); `delayed_only` applies on both (schedule: latest variance > 0; budget: line funds a currently-delayed PID); rows carry the project name (`agency_project_name` on schedule rows; line-keyed `fms_project_name` — latest non-null, from fb86 — on budget rows); schedule rows carry `forecast_past_due` (see §5)
  Budget metric pair: `budget_variance` (last-period source-LAG delta) vs
  `cumulative_budget_change` (latest − original; original prefers the adopted amount).
- `run_sql` — read-only SELECT against the DuckDB; `inline` / `csv` / `xlsx` export. Each export writes a fresh uniquely-named file under `exports/`. All three modes enforce the same query timeout (`RUN_SQL_TIMEOUT_SECONDS`, 30s) — a runaway query is interrupted, not left to hang. The read-only guard ignores string literals and comments (a literal `'%update%'` is fine), and its rejection message steers DESCRIBE/SHOW/PRAGMA callers to `describe_table`; xlsx stringifies LIST/STRUCT cells. The docstring steers callers to the TYPED tables first and carries the two grain rules (budget comparisons key on `(managing_agency, fms_id)`; sponsor-scoped budget sums use the `fms_sponsor` semi-join, never a value-bearing join). Every inline result echoes `latest_reporting_period`, and adds a `period_basis_note` when the query counts an all-history dimension (`fms_location` / `fms_sponsor` / `lifetime_budget_variance`) — those have no `reporting_period` column, so a raw count spans all periods, not one; truncated inline results carry a `truncation_note` steering to `output='csv'`

All tools carry MCP `ToolAnnotations` (read-only + idempotent; `run_sql` flagged non-idempotent for its export file writes).
All 18 tools publish typed success `outputSchema` definitions and return matching
`structuredContent` and serialized JSON text. Choice fields use enumerated inputs;
listing limits are bounded integers. Shared tool validation rejects invalid choices,
roles and parameter combinations. Expected business/validation failures set MCP
`isError=true`, including unknown identifiers and absent comparison snapshots.

`rank_projects` and `project_portfolio` expose `population_scope='latest_known'`
(compatible default: each entity's own latest observation) or `'current'` (values
from the selected complete snapshot). Rows include `reporting_period` and
`present_in_current_snapshot`; responses identify `current_period`. Current scope
uses snapshot schedule/budget values and funding, with cumulative schedule variance
stopping at that period. Latest-known presence flags test membership in the complete
snapshot even if a newer partial snapshot also contains the entity. Schedule category
filters use that state's current funding links by default; `category_scope='all_history'`
explicitly includes former memberships. Budget categories remain one per full line key.
Current budget rankings derive sponsor ownership and delayed-project filters from
the independently selected complete schedule snapshot, using full budget-line keys;
they echo the schedule ownership/filter period when it differs from the budget period.

`schedule_breakdown(metric='count')` requires `statistic='count'`. Schedule changes
allow an empty baseline only when it strictly predates the earliest available
snapshot; an absent interior baseline errors and lists available periods. A PID
absent from an otherwise valid snapshot retains the documented change semantics.
Budget comparisons reject malformed/off-cadence periods and retain null deltas for
valid periods with no data.

`run_sql` query connections disable external file/network access and automatic
extension installation/loading, with that configuration locked before queries run.
CSV and XLSX files are written by application code to controlled export paths;
submitted SQL receives no filesystem permission for exports.

Budget `delayed_only` rankings match the complete `(managing_agency, fms_id)` key
against each delayed PID's **own latest link-period** funding set. Removed links
and another holder's same-ID line are excluded; shared current funding counts once.

## 2. Core model: PID vs FMS, and the many-to-many relationship

- **PID = a SCHEDULE** (what's built and when); **FMS ID = a BUDGET** (a funding source).
- **They are many-to-many:** one FMS ID may fund several PIDs; one PID may be funded by
  several FMS IDs. Most are 1:1; ~3% fan out — **list all, never silently pick one.**
- **Directionally asymmetric.** Dashboard PIDs ordinarily have funding links (0 PIDs
  lack a budget at 202601). Source-only schedules and historical exceptions can lack
  a matching dashboard edge. The reverse fails often: **~45% of budget lines have no
  schedule** (2,497 of 5,490 in the join at 202601), because a budget can exist before its
  project reaches **Design** (when schedule reporting starts) and pass-through / expense /
  certain line types never require a schedule. A budget with no PID is **normal, not missing
  data**.
- Schedule questions read the PID side; budget questions read the FMS side.
- `managing_agency` = **executor** on schedule rows, **budget-holder** on budget rows
  (a budget-holder does not necessarily build anything).
- **Budget grain is (managing_agency, fms_id)** — the same FMS ID appears under multiple
  agencies in a single period, so period-over-period budget comparisons must key on the
  pair, never on `fms_id` alone.
- **Location (borough, community board) keys to the BUDGET LINE**, not the PID — the
  schedule dataset carries no location columns at all.
  `fms_location` holds the line-level value. A PID's scalar `borough` is derived from
  its lines (*specific-beats-Citywide*): exactly one specific borough → that borough
  (a citywide umbrella line doesn't relocate the work); 2+ specific boroughs →
  `'Multiple'`; only Citywide → `'Citywide'`. The full set is always in `boroughs`.
- **Original budget ≠ first snapshot.** qj5n rows with NULL spend are imports from a
  separate first-budget system (adoption month as pseudo-period, any calendar month).
  They live in `original_budget`; `lifetime_budget_variance.original_budget` prefers
  the adopted amount (`original_budget_source='adopted'`, else `'first_snapshot'`).

### The four source datasets and their grain

| Socrata dataset | Shape | Key / grain | PID | FMS ID |
|---|---|---|:--:|:--:|
| Citywide **Schedule History & Variance** (`95tx`) | pure SCHEDULE | PID | ✅ | ❌ |
| Citywide **Budget Spend History & Variance** (`qj5n`) | pure BUDGET | `(fms_id, managing_agency)` | ❌ | ✅ |
| Citywide **Budget by Fiscal Year** (`gyhf`) | pure BUDGET (city / non-city FY split) | `(fms_id, managing_agency, fiscal_year)` | ❌ | ✅ |
| Citywide **Budget and Schedule** (`fb86`) | the **JOIN** of schedule × budget | PID × FMS pair (+ line / community-board splits) | ✅ | ✅ |

- **`fb86` is a join**, so a PID or an FMS ID **repeats across rows** on m-to-m fan-out,
  budget-line/borough splits, or budget-only rows with a **NULL `pid`**. **Dedup before
  counting** — distinct PID for schedule counts; distinct `(fms_id, managing_agency)` for
  budget sums.
- It is the **only place `fms_id` and `sponsor_agency` co-occur** — hence `fms_sponsor` is
  derived from it (§4).
- **Managing agency is defined by the schedule side only** — the 13 `is_schedule_executor`
  agencies in `95tx`. The budget side's `managing_agency` is a budget-holder label whose
  ~25-name superset includes ~12 client/holders that never manage a schedule (full list and
  the attribution-lens nuance in §5).

These four raw datasets are normalized into the tables in §6.

## 3. Sponsor-driven category taxonomy

- **21 program/facility categories** (Library, Parks & Recreation, Sewer & Water, Bridges,
  Streets & Highways, Jails & Correction, …), materialized once into `category_dim` from
  the curated `data/categories.yaml`.
- **Three primary signals** (project names and current holders can reassign / undercount):
  `ten_year_plan_category` keyword, fms-id/budget-line prefix (LB/LN/LQ, HB/BR, WP/WM/SE…),
  and `sponsor_agency`.
- **Three-tier precedence:** specific keyword/prefix → **sponsor routing** → generic
  facility keyword → `Other / Uncategorized`.
- **Category grain is `(managing_agency, fms_id)`.** Each line uses its own latest
  nonempty ten-year labels and sponsors, resolved independently by attribute.
  All values tied at that period are eligible: match any label or atomic owner,
  then resolve conflicts by taxonomy tier and YAML file order. Source row order
  never selects a winner. Comma-separated sponsors are trimmed and normalized to
  uppercase; co-owners use the same precedence. If no sponsor is known, only that
  line's holder supplies the sponsor-tier fallback. Labels and ordinary owners
  never transfer to another holder's line. Category joins require both key columns.
- **Sponsor drives the type for institution categories.** Owner-authoritative categories
  declare `ever_managed_by` (all-history across holders of an FMS ID; survives
  budget-holder reassignment). This is the deliberate exception to line-local signals. Applied to
  **Library** (BPL/NYPL/QPL/NYRL) and **Cultural Institutions** (DCLA). Consequences:
  - A DCLA energy retrofit rolls up to **Cultural**, not Energy.
  - A DEP green-infrastructure project (e.g. Tibbetts Brook daylighting, a CSO/stormwater
    relief project) routes to **Sewer & Water**, not Parks.
  - "Park Pedestrian Bridges" are **Bridges** (structurally bridges), not Parks.
- **~99.3% of budget categorized**; the remainder is the city's own undifferentiated
  "MISCELLANEOUS" labels.

## 4. Agency attribution — sponsor (owner) vs managing (builder)

- **Two roles per project.** `sponsor_agency` = the agency that **owns/funds** the project
  (the stable signal). `managing_agency` = the **executor** on schedule rows and the
  **budget-holder** on budget rows — a manager need not own anything, a budget-holder need
  not build anything.
- **Two agency classes.** *Owner-agencies* (DOC, DEP, DOT, DPR, NYPD, FDNY, DOHMH, DHS,
  DSNY, CUNY, the libraries, DCLA, …) sponsor their own work and either self-manage
  (`managing = sponsor`) or delegate construction while staying the sponsor. *Manager-as-a-
  service agencies* (**DDC, DCAS, EDC**) build for others; the manager-≠-sponsor role is
  near-monopolized by these three.
- **Role-aware default.** "Agency X's projects" resolves to `managing_agency = X` for the
  three manager agencies (tagged `role_default: managing` in `data/agencies.yaml` →
  `agency_dim`) and to `sponsor_agency = X` for everyone else. A bare `managing_agency`
  filter undercounts owner-agencies badly — e.g. it sees only the ~15 jail-adjacent
  projects DOC builds itself and **misses the $4.47B Borough-Based Jails** that DDC manages
  for DOC. Agency-scoped tools accept `agency` + `agency_role` (`auto | sponsor | managing`)
  and echo an `agency_scope` block so the lens is always explicit.
- **`fms_sponsor` — the budget-side owner bridge (and why it's efficient).** Budget tables
  carry no sponsor, only the budget-holder. `fms_sponsor` (`fms_id, sponsor_agency`) is a
  materialized index derived once per build from `latest_project_state.linked_budgets`
  (pid_funding's per-PID current link set, ultimately `raw_project_detail`, the only place
  `fms_id` and `sponsor_agency` co-occur) — **each PID's own latest link snapshot**, the
  same single rule that drives `attributed_budget` (a global latest-period filter would
  drop every line whose links last appeared in an earlier period — ~200 still-budgeted
  lines), with composite comma-joined sponsor strings split into atomic rows. It turns a sponsor-scoped budget query into a cheap **semi-join** `fms_id IN (SELECT
  fms_id FROM fms_sponsor WHERE sponsor_agency = X)` instead of an `FMS → PID → sponsor` join
  recomputed on every call. Use the semi-join form, **never a value-bearing `JOIN … USING
  (fms_id)` feeding a `SUM`** — an `fms_id` spans multiple `(fms_id, managing_agency)` budget
  rows, so a join would fan out and double-count. It is kept as a **separate table rather
  than a column on `lifetime_budget_variance`** precisely because sponsor is many-to-many
  with a budget line (e.g. `BBJ-Q` → {DOC, DEP}) — folding it in would corrupt that table's
  `(fms_id, managing_agency)` grain or collapse the M:M.
- **Caveats.** Multi-sponsor lines (2 at 202601: `BBJ-Q`, `TLCWOOD1`) appear under each
  owner at full line value — never sum a shared line across agencies. A handful of PIDs
  carry comma-joined `sponsor_agency` strings (e.g. `'DOT, DPR'`) — matched via atomic split,
  not equality. Budget-only lines with no linked PID have no sponsor and are reachable only
  via `managing_agency` — including lines whose PID links exist only in past snapshots (no
  PID's CURRENT link set lists them), which are budget-only as of the current period.

## 5. Other built-in domain rules

- **Neutral, signed reporting** — "moved 45 days later", "budget grew $2.1M"; never echo
  loaded words ("slippage", "overrun"), which map only to the increasing side.
- **Presence is period-specific** — presence in a selected snapshot means reportable
  at that period. A historical or latest-known row does not prove current presence;
  there is no separate active flag.
- **Lifecycle & reporting obligation.** Phases run **Pre-Design → Design → Construction
  Procurement → Construction → Close-out**. A **schedule (PID) is reported only from the
  start of Design through the end of Construction** — Pre-Design and Close-out carry no
  schedule progression (NULL milestones there are suppressed-by-rule, not missing). The
  construction end date is the **"substantial completion"** date (`actual_construction_end`).
  A **budget (FMS) is reported as long as its funding line is active**, which outlives
  construction — so a finished project (`lifecycle_status = 'completed'`) **stays present,
  sometimes for years**, because its budget line is still open. *Completed-but-present is
  normal:* presence means an active budget line, not active construction — never read a
  present project as work-in-progress. (In the 202601 dashboard snapshot, 755 PIDs are
  `completed`.)
- **Reporting cadence ends 01/05/09** (Jan/May/Sep) — the whole report publishes **3×/year,
  mandated by the City's Commitment Plan**; **spend reports only those periods.**
- **Null forecast dates usually mean "suppressed," not "missing."**
- **`forecast_past_due`** (on `latest_project_state`; surfaced by `get_project_schedule`,
  `project_portfolio`, `rank_projects` schedule rows, and `get_project_history.current_state`)
  — TRUE iff ALL three guards hold: (1) `lifecycle_status = 'in_progress'` AND
  `completion_date_type IS DISTINCT FROM 'Actual'` — a project completed/cancelled by ANY
  signal is NEVER past due, including the edge where an Actual completion date exists but
  the phase label hasn't flipped; (2) `forecast_completion IS NOT NULL`; (3)
  `forecast_completion` < the first day of the PID's OWN latest `reporting_period` month
  (own-period basis, NOT the global latest period — 543 PIDs left the reporting universe
  earlier; an in-month forecast is "due now", not past due). Never NULL — always TRUE/FALSE.
- **RAW tables are all VARCHAR** — cast in SQL (e.g. `CAST(total_budget AS DOUBLE)`).
- **13 schedule-executor agencies** — these **are** the distinct `managing_agency` values
  in the *schedule* dataset (CUNY, DCAS, DDC, DEP, DHS, DOC, DOHMH, DOT, DPR, DSNY, EDC,
  FDNY, NYPD), and exactly the set flagged `is_schedule_executor` in `agency_dim`. The
  *budget* dataset's `managing_agency` is a **superset (~25)**: the same 13 plus 12
  budget-holders/clients (ACS, BPL, DCLA, DFTA, DOE, HHC, HPD, HRA, NYPL, NYRL, OTI, QPL)
  that **never manage a schedule** — so a name appearing only in budget-side `managing_agency`
  is a budget-holder, **not** a real manager. A client can land here when it holds a budget
  line before any schedule exists (early allocation). (Being a schedule executor is a separate question from the attribution lens:
  per §4 only DDC/DCAS/EDC default to the *managing* view; the other 10 are owner-agencies
  whose "their projects" defaults to *sponsor*.)
- **Schedule history is floored at 202305** (the window's first period) — cumulative
  slippage before that is truncated (a floor, not the true project baseline).
- **Variance is period-over-period by default**; cumulative sums the available,
  artifact-guarded variance observations in the stated schedule universe. Because
  source observations can be omitted from the dashboard, that sum need not equal
  the native source's lifetime total or a simple endpoint subtraction. Cumulative
  rankings explicitly label this basis.
- Partner-managed / budget-only (no-PID) FMS lines are normal on the budget side.
- Some categories are filtered out upstream before publication — the datasets carry
  only the city's "reportable" set.
- **Parenthesized `current_phase` values are no-schedule REASONS, not phases**:
  typically used where a line has no reported schedule, with historical exceptions
  on rows that have a PID. They remain verbatim before display-phase normalization,
  including `(Pre-Design)`, `(Design)`, `(Construction)` and `(Closeout)`.
  Where a PID's FMS rows disagree, the displayed phase prefers a real phase over
  a parenthesized reason; `lifecycle_status` is the
  authoritative completion signal and may legitimately differ from the displayed phase.
- **Suppression rule (precise):** forecasts and most actual milestones publish NULL
  when the PID is design-build, in Pre-Design, or all its lines carry a non-exempt
  no-schedule category; **`actual_construction_end` is never suppressed** — its NULLs
  are genuinely missing.
- **95tx variance compares consecutive AVAILABLE forecasts** (the LAG skips
  suppressed/missing-forecast snapshots), not strictly adjacent periods.
- **Reportable-set asymmetry (observed):** fb86 history is frozen as published,
  while qj5n's history reflects TODAY'S reportable set retroactively — the same
  past period can differ between the two datasets; gyhf rows track the
  (FMSID, managing agency) pair.
- **Known upstream defect:** Manhattan lines without a specific community board
  publish `community_board` NULL (224 rows at 202601), while the other boroughs
  publish a borough-wide placeholder.
- **95tx `managing_agency` is the schedule-reporting agency**, not the FMS
  budget-holder; the published header is misleading.
- **Two budget-variance bases:** `budget_variance` / `over_budget` = vs the PREVIOUS
  reporting period (by design); `cumulative_budget_change` = latest − original.
  Always state which basis is being reported.

## 6. Data, freshness, provenance

- **4 Socrata datasets** → normalized tables (`schedule_history`, `budget_history`,
  `schedule_budget_link`, `project_budget_fy`) + analytics rollups
  (`latest_project_state`, `cumulative_schedule_variance`, `lifetime_budget_variance`,
  `agency_rollup_by_period`, `pid_funding`, `fms_sponsor`, `fms_location`,
  `original_budget`, `source_schedule_history`, `schedule_source_coverage`) + dimensions
  and metadata (`agency_dim`, `category_dim`, `meta`, `data_build`). `fms_sponsor` (`fms_id → sponsor_agency`) is a precomputed
  owner-attribution index for the budget side (see §4).
- **Schedule universe:** schedule statistics retain the dashboard-aligned fb86
  PID/period spine. `source_schedule_history` retains all native 95tx observations,
  with `in_dashboard`; `schedule_source_coverage` reconciles matched, source-only
  and dashboard-only rows by PID, latest periods and both cumulative variance bases.
  Schedule MCP answers state this universe and carry coverage; PID detail/history
  makes omitted observations inspectable, including wholly source-only PIDs.
  `dataset_info.available_periods` follows each independent typed source, not a shared
  dashboard period list.
- **Atomic-swap ingest** (build a shadow DB, then atomically replace) so the live server
  never reads a half-built database. Publication requires a valid, checkpointed,
  closed shadow on the same filesystem. The live image must also be checkpointed
  and read-only; an active writer or WAL prevents backup/publication. A per-target
  OS lock serializes publishers;
  an overlapping publication fails with a retry message. A complete backup is staged
  and installed as `.bak` while the live path remains available, followed by one
  `os.replace(shadow, live)`. Failure before publication preserves live and shadow;
  `.bak` may then equal live. Retry the retained shadow, or copy `.bak` to a fresh
  shadow for rollback. Existing readers finish on their old image; same-process
  DuckDB connections may retain that image until all old connections close.
- **Ingest isolation and health:** a separate per-target OS lock covers the entire
  ingest/rematerialize operation, rejecting overlap before downloads. Each run owns
  a unique directory and shadow. Every parsed CSV page must match the declared
  header and row width; stable metadata revisions and source row counts bracket
  downloading. Before materialization/publication, health checks validate counts,
  required keys, duplicate declared source keys and reporting-period coverage,
  including disappeared prior snapshots and a partial newest snapshot. Original-budget
  adoption months remain separate. Different complete periods across sources are
  allowed with a warning. Failed runs retain their own downloads/shadow and diagnostic
  report; successful runs retain the before/after health report.
- **Schema version 4** includes the category dimension's complete budget-line key,
  source schedule reconciliation and completed build identity.
  Existing databases must be rebuilt before the new server can serve them; the
  schema guard reports an actionable error for older builds.
- **Curated dictionaries:** `data/agencies.yaml` (agencies), `data/categories.yaml`
  (program categories), and `data/data_dictionary.yaml` (field definitions), each loaded
  at build time; plus `data/tables.yaml` — the `describe_table` catalog (grain / keying /
  column notes) — which is loaded at CALL time (not build time) and overlaid on live
  `information_schema` columns. These four YAML files and `fms_agency_dim.tsv` are
  bundled in the wheel and loaded through package resources outside a checkout;
  source checkouts continue to edit `data/` directly.
- **Field definitions:** `data/data_dictionary.yaml` is a one-time extract of the dataset's
  official NYC Open Data data-dictionary XLSX → the `column_dict` table, surfaced via
  `describe_field` and folded into `dataset_info`. Static/curated (re-extract by hand on a
  version bump — it revises ~yearly). A build/test guard (`dictionary_drift`) keeps the YAML
  in sync with the table schema; an upstream source-schema change (including a column
  REORDER, which `read_csv(columns=…)` would otherwise map positionally and silently
  scramble) fails the ingest via the header-order assertion in `load_raw_csv`.
- **Reproducible answers:** query answers supply self-contained `reproduce_sql` and
  component SQL where a response uses several queries: portfolio rows/full summary/
  deduplicated budget, history snapshots/adoption headers/current state/links, and
  resolver totals. Every MCP success and export carries a completed `data_build`
  identity, source revisions and reporting periods. Static catalog text identifies its
  curated source instead of claiming a SQL-only reproduction. Build identity hashes
  the schema, materialization code, curated resources and source revisions; a rule
  change is detectable independently of upstream timestamps.
  The fingerprint is captured before materialization and checked again before
  stamping; changed rules abort publication. A diagnostic or cleanup failure after
  successful publication is reported as a warning without mislabeling the committed
  database update as a failure.
- **Safe rematerialization:** `od-cpd rematerialize` validates complete local raw
  inputs, rebuilds an isolated shadow and atomically publishes without downloading.
  It preserves source-ingestion timestamps. `update` chooses this path when rules
  changed but source revisions did not. Reconnect stdio clients after code changes.
- **Freshness:** `status` is explicitly local-only. `status --check-upstream` compares
  revisions with Socrata and separately reports ingestion time, source revision,
  complete/observed reporting periods and partial-publication warnings. Results
  distinguish newer revisions, verified up-to-date data, unreachable sources and
  inconclusive checks when a source changes during verification.
- **CSV / XLSX export** via `run_sql`: CSV preserves DuckDB text values and formula-like
  source strings, with quoted empty strings distinct from unquoted NULLs; it does not
  alter values for spreadsheet interpretation. CSV provenance is saved in an adjacent
  JSON sidecar. XLSX writes all string cells, headers and methodology as literal text,
  including formula/error-looking values, while preserving numeric/date cells.
- **Golden evals:** `tests/evals/` replays real analytical questions through the tool
  functions against the live DB, asserting headline numbers AND rule conveyance
  (M:M list-all, signed envelopes, agency_scope, variance basis). Pinned to one
  snapshot period; skips (with a re-pin pointer) after a newer ingest — see
  `tests/evals/README.md`.
- **Pull-request CI:** a locked Python 3.12/3.14 matrix runs deterministic synthetic
  behavior tests, exact 18-tool inventory/schema checks, a real stdio protocol test
  and an isolated installed-wheel test. Missing or stale snapshot golden coverage is
  explicit in pytest's skip summary; core contract tests require no local database.
- **Every published column is keyed to a specific entity** (budget line, PID,
  pair, or snapshot); the materialization in `src/od_cpd/materialize.py`
  encodes those keying assumptions.
