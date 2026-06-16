# OD-CPD MCP — Feature & Rules Reference

> **Maintenance:** keep this current. Whenever the MCP gains a tool, a built-in
> domain rule, or a taxonomy/behavior change, update the relevant section below
> and bump the "Last updated" date. This file is the canonical inventory of what
> the server does and the rules it encodes.
>
> _Last updated: 2026-06-16_

The MCP serves NYC Capital Projects data (4 Socrata datasets) over a local DuckDB,
with domain rules baked in so callers don't have to rediscover them.

## 1. Tools (the callable surface)

**Discovery / metadata**
- `dataset_info` — per-dataset freshness, current period, row counts, key caveats, per-dataset field definitions, and `domain_rules` (the full primer, embedded so clients that drop MCP server instructions still get the rules on the first orienting call)
- `list_agencies` — agency dictionary (from `agencies.yaml`) + live CPD presence + schedule-executor flag
- `list_categories` — program/facility categories with budget-line counts & totals
- `describe_field` — official field definitions (description, allowed values, primary/foreign key, limitations, notes), filterable by field and/or dataset (both filters are case-insensitive SUBSTRING matches)

**Resolution & detail**
- `resolve_project_reference` — any PID / FMS ID / name / partial → schedule + budget matches, bucketed. `matched_field` is computed per row (`pid` / `fms_id` / name / description); LIKE wildcards in the query (`%`, `_`) match literally
- `get_project_schedule` (PID) / `get_project_budget` (FMS line) — full detail + linked counterparts. Both sides list only counterparts from the anchor's LATEST link period (stale links are not current); FMS ids match case-insensitively

**Schedule analytics**
- `schedule_breakdown` — counts/averages by agency/sponsor/borough/phase/category. For `metric='schedule_variance'`, `statistic` ∈ {count, mean, median, sum, min, max} — anything else errors (no silent fallback); `count` results carry no direction (unsigned). Category grouping counts a PID once in EACH of its line-derived categories (non-additive, caveat in-band)
- `schedule_changes` — newly completed / newly delayed between two periods. BOTH change types compare `from_period` → `to_period` ("newly delayed" = positive variance at `to`, none at `from`). Periods are validated: off-cadence, inverted, or missing-`to_period` values error; a `from_period` predating the data is allowed and noted
- `delay_reason_stats` — distribution of delay reasons
- `project_duration_stats` — duration between two actual milestones; optional `group_by` (`managing_agency` | `borough` | `lifecycle_status`) returns per-group stats

**Budget analytics**
- `budget_breakdown` — budget/spend by agency or category, deduped on (fms_id, managing_agency); category is line-grain via `category_dim` (one category per line — additive)
- `budget_change` — Δ budget/spend for an agency or FMS line between periods

**Portfolio**
- `project_portfolio` — PID-grain cross-section: filter by category ∩ borough ∩ community_board ∩ lifecycle_status ∩ agency(+role), ordered by nearest completion (NULLs last). Borough filters match the line-derived `boroughs` list (multi-borough PIDs found by any borough); summary covers the FULL filtered set with BOTH budget bases — per-PID `attributed_budget_total` (shared lines count on each PID) and deduped `line_budget_total` (the cash view). Replaces the recurring "category ∩ borough ∩ status, schedule⨝budget, by completion" `run_sql` pattern

**Ranking & raw**
- `rank_projects` — rank schedules (PIDs) or budgets (FMS lines); supports a `category=` filter. `min_total_budget` / `max_total_budget` apply on both entities (schedule: `attributed_budget`; budget: `latest_budget`); `delayed_only` applies on both (schedule: latest variance > 0; budget: line funds a currently-delayed PID)
  Budget metric pair: `budget_variance` (last-period source-LAG delta) vs
  `cumulative_budget_change` (latest − original; original prefers the adopted amount).
- `run_sql` — read-only SELECT against the DuckDB; `inline` / `csv` / `xlsx` export. Each export writes a fresh uniquely-named file under `exports/`. The read-only guard ignores string literals and comments (a literal `'%update%'` is fine); xlsx stringifies LIST/STRUCT cells. The docstring steers callers to the TYPED tables first and carries the two grain rules (budget comparisons key on `(managing_agency, fms_id)`; sponsor-scoped budget sums use the `fms_sponsor` semi-join, never a value-bearing join). Every inline result echoes `latest_reporting_period`, and adds a `period_basis_note` when the query counts an all-history dimension (`fms_location` / `fms_sponsor` / `lifetime_budget_variance`) — those have no `reporting_period` column, so a raw count spans all periods, not one

## 2. Core model: PID vs FMS, and the many-to-many relationship

- **PID = a SCHEDULE** (what's built and when); **FMS ID = a BUDGET** (a funding source).
- **They are many-to-many:** one FMS ID may fund several PIDs; one PID may be funded by
  several FMS IDs. Most are 1:1; ~3% fan out — **list all, never silently pick one.**
- **Directionally asymmetric.** Every PID has **≥1 FMS** ("no budget, no work" — 0 PIDs
  lack a budget at 202601). The reverse fails often: **~45% of budget lines have no
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
- **Three signals — never `managing_agency` or project name** (which reassign / undercount):
  `ten_year_plan_category` keyword, fms-id/budget-line prefix (LB/LN/LQ, HB/BR, WP/WM/SE…),
  and `sponsor_agency`.
- **Three-tier precedence:** specific keyword/prefix → **sponsor routing** → generic
  facility keyword → `Other / Uncategorized`.
- **Sponsor drives the type for institution categories.** Owner-authoritative categories
  declare `ever_managed_by` (all-history; survives budget-holder reassignment). Applied to
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
- **Presence = the active flag** — every project here is a currently-reportable active
  capital project; there is no separate status flag.
- **Lifecycle & reporting obligation.** Phases run **Pre-Design → Design → Construction
  Procurement → Construction → Close-out**. A **schedule (PID) is reported only from the
  start of Design through the end of Construction** — Pre-Design and Close-out carry no
  schedule progression (NULL milestones there are suppressed-by-rule, not missing). The
  construction end date is the **"substantial completion"** date (`actual_construction_end`).
  A **budget (FMS) is reported as long as its funding line is active**, which outlives
  construction — so a finished project (`lifecycle_status = 'completed'`) **stays present,
  sometimes for years**, because its budget line is still open. *Completed-but-present is
  normal:* presence means an active budget line, not active construction — never read a
  present project as work-in-progress. (At 202601, ~1,221 present PIDs are already
  `completed`.)
- **Reporting cadence ends 01/05/09** (Jan/May/Sep) — the whole report publishes **3×/year,
  mandated by the City's Commitment Plan**; **spend reports only those periods.**
- **Null forecast dates usually mean "suppressed," not "missing."**
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
- **Variance is period-over-period by default**; cumulative = telescoped sum of per-period
  variance = latest forecast minus earliest reported forecast.
- Partner-managed / budget-only (no-PID) FMS lines are normal on the budget side.
- Some categories are filtered out upstream before publication — the datasets carry
  only the city's "reportable" set.
- **Parenthesized `current_phase` values are no-schedule REASONS, not phases**:
  they appear only when no schedule (PID) was reported for that
  line. `(Completed)`/`(Closeout)` = finished before an update was requested; reason
  values naming an active phase (`(Design)`, `(Construction)`, `(Initiation)`) are
  upstream data-entry errors. They occur only on NULL-PID rows, so they never enter
  `schedule_history`. Where a PID's FMS rows disagree on phase (86 historical
  PID-periods), the displayed phase prefers the real phase; `lifecycle_status` is the
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
  `original_budget`) + dimensions (`agency_dim`,
  `category_dim`, `meta`). `fms_sponsor` (`fms_id → sponsor_agency`) is a precomputed
  owner-attribution index for the budget side (see §4).
- **Atomic-swap ingest** (build a shadow DB, then atomically replace) so the live server
  never reads a half-built database.
- **Curated dictionaries:** `data/agencies.yaml` (agencies), `data/categories.yaml`
  (program categories), and `data/data_dictionary.yaml` (field definitions), each loaded
  at build time.
- **Field definitions:** `data/data_dictionary.yaml` is a one-time extract of the dataset's
  official NYC Open Data data-dictionary XLSX → the `column_dict` table, surfaced via
  `describe_field` and folded into `dataset_info`. Static/curated (re-extract by hand on a
  version bump — it revises ~yearly). A build/test guard (`dictionary_drift`) keeps the YAML
  in sync with the table schema; an upstream source-schema change (including a column
  REORDER, which `read_csv(columns=…)` would otherwise map positionally and silently
  scramble) fails the ingest via the header-order assertion in `load_raw_csv`.
- **Every answer carries a provenance block** with a self-contained `reproduce_sql`.
- **CSV / XLSX export** via `run_sql`.
- **Golden evals:** `tests/evals/` replays real analytical questions through the tool
  functions against the live DB, asserting headline numbers AND rule conveyance
  (M:M list-all, signed envelopes, agency_scope, variance basis). Pinned to one
  snapshot period; skips (with a re-pin pointer) after a newer ingest — see
  `tests/evals/README.md`.
- **Every published column is keyed to a specific entity** (budget line, PID,
  pair, or snapshot); the materialization in `src/od_cpd/materialize.py`
  encodes those keying assumptions.
