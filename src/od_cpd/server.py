# src/od_cpd/server.py
from __future__ import annotations

import json
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import contracts as C
from .coverage import attach_schedule_coverage
from .dbio import ro_conn, DBMissingError
from .export import write_csv, write_xlsx
from .primer import PRIMER
from .provenance import enrich_provenance
from .tools import lookup
from .tools.sql import run_sql_on, validate_select
from .tools.resolve import resolve_from
from .tools.inspect import (get_project_schedule_from, get_project_budget_from,
                            get_project_history_from)
from .tools.schedule import (schedule_breakdown_from, schedule_changes_from,
                             delay_reason_stats_from)
from .tools.budget import budget_breakdown_from, budget_change_from
from .tools.ranking import rank_projects_from
from .tools.lifecycle import project_duration_stats_from
from .tools.portfolio import project_portfolio_from

mcp = FastMCP("od-cpd", instructions=PRIMER)

# All tools are read-only queries over a read-only connection. run_sql is the one
# exception: its csv/xlsx modes write a fresh file under exports/ per call —
# additive (never destructive), but neither read-only nor idempotent.
# openWorldHint=False everywhere: the local DuckDB is a closed world (no network/
# external effects), so results depend only on the bundled database.
_READONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
_RUN_SQL_NOTES = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                 idempotentHint=False, openWorldHint=False)


@mcp.tool(annotations=_RUN_SQL_NOTES)
def run_sql(query: str, output: Literal["inline", "csv", "xlsx"] = "inline") -> C.SQLResult:
    """Run a read-only SELECT against the local CPD DuckDB.

    output: 'inline' (default, rows capped) | 'csv' | 'xlsx' (writes a file, returns path).
    PREFER the typed tables: latest_project_state (1 row/PID, borough+boroughs,
    attributed_budget), schedule_history (PID x period), budget_history (snapshot rows
    at the (fms_id, managing_agency) x period grain), original_budget (adopted first
    budgets), lifetime_budget_variance (per-line lifetime, original_budget_source),
    schedule_budget_link (PID<->FMS edges), pid_funding (per-PID link rollup),
    cumulative_schedule_variance (per-PID lifetime days), fms_sponsor (fms_id -> owner),
    fms_location (line-level borough/community board/name), agency_rollup_by_period,
    category_dim, agency_dim, project_budget_fy, meta, data_build,
    source_schedule_history (95tx-native observations, in_dashboard flag),
    schedule_source_coverage (PID-level reconciliation and both cumulative bases).
    GRAIN RULES: budget comparisons key on (managing_agency, fms_id) — never fms_id
    alone; sponsor-scoped budget sums use the semi-join
    fms_id IN (SELECT fms_id FROM fms_sponsor WHERE sponsor_agency = ...) — a
    value-bearing JOIN fans out across a line's agency rows and double-counts.
    PERIOD BASIS: fms_location, fms_sponsor, lifetime_budget_variance are ALL-HISTORY
    dimensions (latest row per line/owner, NO reporting_period column) — JOIN them to
    enrich or for lifetime figures; do NOT COUNT them as a single period's inventory.
    For a period count, aggregate raw_project_detail / schedule_history / budget_history
    filtered by reporting_period. Every result echoes latest_reporting_period (and warns
    via period_basis_note when a query counts an all-history dim) — state the basis.
    RAW mirrors (raw_project_detail, raw_budget_fy, raw_budget_history,
    raw_schedule_history) are all VARCHAR — cast as needed.
    """
    try:
        with ro_conn() as con:
            if output == "inline":
                return enrich_provenance(con, run_sql_on(con, query))
            q = validate_select(query)
            prov = enrich_provenance(con, {"provenance": {
                "definition": "run_sql export (full result set)", "reproduce_sql": q}})["provenance"]
            if output == "csv":
                path = write_csv(con, q)
                provenance_file = path.with_suffix(path.suffix + ".provenance.json")
                provenance_file.write_text(json.dumps(prov, indent=2, default=str) + "\n", encoding="utf-8")
            elif output == "xlsx":
                # provenance describes the full export; don't re-run the query
                path = write_xlsx(con, q, prov)
            else:
                raise ToolError(f"unknown output mode: {output}")
            result = {"file": str(path), "provenance": prov}
            if output == "csv":
                result["provenance_file"] = str(provenance_file)
            return result
    except DBMissingError as e:
        raise ToolError(str(e)) from e


def _with_conn(fn, *args, **kwargs):
    try:
        with ro_conn() as con:
            result = fn(con, *args, **kwargs)
            if fn in (schedule_breakdown_from, schedule_changes_from, delay_reason_stats_from,
                        project_duration_stats_from, project_portfolio_from) or (fn is rank_projects_from and args[0] == "schedule"):
                result = attach_schedule_coverage(con, result)
            if "error" in result:
                raise ToolError(str(result["error"]))
            return enrich_provenance(con, result)
    except DBMissingError as e:
        raise ToolError(str(e)) from e


@mcp.tool(annotations=_READONLY)
def dataset_info() -> C.DatasetInfoResult:
    """Per-dataset freshness, current period, row counts, and the key caveats."""
    return _with_conn(lookup.dataset_info_from)


@mcp.tool(annotations=_READONLY)
def list_agencies(contains: str | None = None) -> C.AgenciesResult:
    """Agency dictionary with live CPD presence + schedule-executor flag."""
    return _with_conn(lookup.list_agencies_from, contains=contains)


@mcp.tool(annotations=_READONLY)
def list_categories() -> C.CategoriesResult:
    """Program/facility categories (Library, Parks & Recreation, Sewer & Water, …)
    with budget-line counts and total budget. Use a category name as the `category`
    filter on rank_projects. Categories are derived from ten_year_plan_category +
    sponsor_agency + fms-id prefix — NOT managing_agency or project name."""
    return _with_conn(lookup.list_categories_from)


@mcp.tool(annotations=_READONLY)
def describe_field(field: str | None = None, dataset: str | None = None) -> C.FieldsResult:
    """Official field definitions (the NYC Open Data data dictionary): description,
    allowed values, primary/foreign key, limitations, notes. Filter by `field` (column
    name or display name) and/or `dataset` (RAW table name or socrata_id); omit both
    for the full dictionary."""
    return _with_conn(lookup.describe_field_from, field, dataset)


@mcp.tool(annotations=_READONLY)
def describe_table(table: str | None = None) -> C.TableResult:
    """Schema catalog for every queryable DuckDB table (typed analytics tables, dims,
    raw mirrors): live columns + types plus curated grain and keying notes. No arg →
    catalog of all tables; table=<name> (case-insensitive) → full detail. Use this
    instead of DESCRIBE/SHOW (blocked in run_sql). Complements describe_field (official
    field semantics for the 4 raw datasets)."""
    return _with_conn(lookup.describe_table_from, table)


@mcp.tool(annotations=_READONLY)
def resolve_project_reference(query: C.Identifier, limit: C.RowLimit = 50,
                              offset: C.Offset = 0) -> C.ResolutionResult:
    """Resolve any project identifier (PID, FMS ID, name, partial) → schedule+budget
    matches bucketed by entity. Call this first for any named-project question."""
    return _with_conn(resolve_from, query, limit, offset)


@mcp.tool(annotations=_READONLY)
def get_project_schedule(pid: C.Identifier) -> C.ScheduleResult:
    """Schedule (PID): phase, lifecycle, signed variance, reason; lists linked budgets;
    `forecast_past_due` flags a forecast already past as of the PID's own latest report
    (never true for completed/cancelled)."""
    return _with_conn(get_project_schedule_from, pid)


@mcp.tool(annotations=_READONLY)
def get_project_budget(fms_id: C.Identifier, managing_agency: str | None = None) -> C.BudgetResult:
    """Budget (FMS line): total, spend, variance; lists linked schedules. NB budget has
    no 'completed' state; spend%=100 ≠ done."""
    return _with_conn(get_project_budget_from, fms_id, managing_agency)


@mcp.tool(annotations=_READONLY)
def get_project_history(pid: C.Identifier | None = None, fms_id: C.Identifier | None = None,
                        managing_agency: str | None = None) -> C.HistoryResult:
    """Period-by-period history for ONE project. Schedule lens (pid=…): each period's
    phase, forecast, signed variance, delay reason + a current-state header carrying
    `agency_project_name`, cumulative variance, and `forecast_past_due` (see
    get_project_schedule). Budget lens (fms_id=…, case-insensitive): each period's
    budget/spend/signed variance per (managing_agency, fms_id) line, the line-keyed
    `fms_project_name`, + the adopted original budget when recorded (adoption-only lines
    return header-only). Provide exactly one of pid/fms_id; managing_agency scopes a
    multi-agency FMS id to one line — otherwise ALL lines are listed."""
    return _with_conn(get_project_history_from, pid, fms_id, managing_agency)


@mcp.tool(annotations=_READONLY)
def schedule_breakdown(group_by: C.ScheduleGroup, metric: C.ScheduleMetric = "count", statistic: C.Statistic = "count",
                       period: str = "current", agency: str | None = None,
                       agency_role: C.AgencyRole = "auto") -> C.BreakdownResult:
    """Counts/averages of schedule metrics by agency/sponsor/borough/phase/category.
    `agency` scopes to one agency; `agency_role` ('auto'|'sponsor'|'managing') picks owner
    vs builder lens (auto: sponsor, except DDC/DCAS/EDC -> managing). Category grouping
    counts a PID once in EACH of its categories (non-additive). Report neutral, signed
    variance."""
    return _with_conn(schedule_breakdown_from, group_by, metric, statistic, period,
                      agency, agency_role)


@mcp.tool(annotations=_READONLY)
def schedule_changes(change_type: Literal["completed", "delayed"], from_period: str, to_period: str,
                     agency: str | None = None, include_cancelled: bool = False,
                     agency_role: C.AgencyRole = "auto") -> C.ChangesResult:
    """Newly completed (DR1) or newly delayed projects between two periods. `agency` scopes
    to one agency; `agency_role` ('auto'|'sponsor'|'managing') picks owner vs builder lens."""
    return _with_conn(schedule_changes_from, change_type, from_period, to_period,
                      agency, include_cancelled, agency_role)


@mcp.tool(annotations=_READONLY)
def delay_reason_stats(period: str = "current", agency: str | None = None,
                       scope: Literal["current", "all_history"] = "current", agency_role: C.AgencyRole = "auto") -> C.ReasonsResult:
    """Distribution of reason-for-delay (only populated when variance>0). Defaults to current
    period; pass scope='all_history' for lifetime. Carries a `coverage` block (delayed_total /
    with_reason / without_reason — the denominator for the distribution, counting delayed rows
    by bare variance_day>0). `agency_role` ('auto'|'sponsor'|'managing') picks owner vs builder
    lens."""
    return _with_conn(delay_reason_stats_from, period, agency, scope, agency_role)


@mcp.tool(annotations=_READONLY)
def budget_breakdown(group_by: C.BudgetGroup = "managing_agency", metric: C.BudgetMetric = "total_budget",
                     period: str = "current", agency: str | None = None,
                     agency_role: C.AgencyRole = "auto") -> C.BreakdownResult:
    """Total budget / spend by managing_agency or category, deduped on (fms_id,
    managing_agency). Category is line-grain (additive). Optional `agency` scopes to one
    agency; `agency_role` ('auto'|'sponsor'|'managing') picks owner vs builder lens. For
    richer cuts use run_sql."""
    return _with_conn(budget_breakdown_from, group_by, metric, period, agency, agency_role)


@mcp.tool(annotations=_READONLY)
def budget_change(target: str, from_period: str, to_period: str,
                  metric: C.BudgetMetric = "total_budget", agency_role: C.AgencyRole = "auto",
                  managing_agency: str | None = None) -> C.BudgetChangeResult:
    """Δ budget/spend for an agency ('agency:DEP') or FMS line ('fms:ABC') between two periods.
    For an agency target, `agency_role` ('auto'|'sponsor'|'managing') picks the lens; sponsor
    scope uses the latest-period owner set (as-of caveat in the result label). An FMS id held
    by several managing agencies is several distinct budget lines: the result then lists
    per-line deltas (never a cross-agency sum); pass `managing_agency` to scope to one line."""
    return _with_conn(budget_change_from, target, from_period, to_period, metric,
                      agency_role, managing_agency)


@mcp.tool(annotations=_READONLY)
def rank_projects(entity: Literal["schedule", "budget"], rank_by: C.RankMetric, n: C.RowLimit = 10, direction: Literal["top", "bottom"] = "top",
                  min_total_budget: float | None = None, max_total_budget: float | None = None,
                  delayed_only: bool = False, category: str | None = None,
                  agency: str | None = None, agency_role: C.AgencyRole = "auto",
                  population_scope: C.PopulationScope = "latest_known",
                  category_scope: C.CategoryScope = "current") -> C.RankingResult:
    """Rank schedules (entity='schedule', rows=PIDs) or budgets (entity='budget', rows=FMS lines).
    rank_by must be NATIVE to entity; the other domain is filter-only. Echoes ranked_entity.
    Budget rank_by: total_budget | spend_to_date | spend_pct | budget_variance
    (last-period delta) | cumulative_budget_change (latest - original budget).
    Optional `category` (see list_categories) filters to one program type, e.g. 'Library'.
    Optional `agency` scopes to one agency; `agency_role` ('auto'|'sponsor'|'managing') picks
    the lens — 'auto' uses the owner (sponsor) view, except DDC/DCAS/EDC default to builder
    (managing). Echoes agency_scope; schedule rows carry `forecast_past_due` — a forecast
    already past as of the observation period (never true for completed/cancelled).
    population_scope='latest_known' uses each entity's own latest observation;
    'current' uses values at the selected complete snapshot. Rows include their
    reporting_period and current-snapshot presence. category_scope='current' uses
    current funding links; 'all_history' also matches removed funding links."""
    return _with_conn(rank_projects_from, entity, rank_by, n, direction,
                      min_total_budget, max_total_budget, delayed_only, category,
                      agency, agency_role, population_scope, category_scope)


@mcp.tool(annotations=_READONLY)
def project_duration_stats(from_milestone: C.Milestone = "actual_design_start",
                           to_milestone: C.Milestone = "actual_construction_end",
                           group_by: C.DurationGroup | None = None) -> C.DurationResult:
    """Duration distribution between two ACTUAL milestones (requires both dates).
    Only forward actual_design_start to actual_construction_end is supported.
    Negative intervals are retained in invalid_intervals and excluded from statistics;
    missing-date and invalid-order counts reconcile to the full latest-known population.
    Optional group_by returns per-group stats instead of the citywide block."""
    return _with_conn(project_duration_stats_from, from_milestone, to_milestone, group_by)


@mcp.tool(annotations=_READONLY)
def project_portfolio(category: str | None = None, borough: str | None = None,
                      community_board: str | None = None,
                      lifecycle_status: C.Lifecycle | None = None,
                      agency: str | None = None, agency_role: C.AgencyRole = "auto",
                      n: C.RowLimit = 50, population_scope: C.PopulationScope = "latest_known",
                      category_scope: C.CategoryScope = "current") -> C.PortfolioResult:
    """Cross-section listing of projects (PIDs): filter by category (see
    list_categories), borough, community_board, lifecycle_status
    ('in_progress'|'completed'|'cancelled'), and/or agency (+agency_role lens);
    rows ordered by nearest completion date (NULLs last). Each row carries schedule
    state + attributed_budget; `summary` covers the FULL filtered set and reports
    BOTH budget bases (per-PID attributed vs deduped line_budget_total). Borough
    matches the PID's boroughs LIST, so multi-borough projects are found by any of
    their boroughs. Rows carry `forecast_past_due` — a forecast already past as of
    the observation period (never true for completed/cancelled). population_scope
    'latest_known' (default) uses each PID's own latest row; 'current' uses values at
    the selected complete snapshot. Rows expose reporting_period and current-snapshot
    presence. category_scope 'current' uses current links; 'all_history' includes
    former category links."""
    return _with_conn(project_portfolio_from, category, borough, community_board,
                      lifecycle_status, agency, agency_role, n, population_scope, category_scope)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
