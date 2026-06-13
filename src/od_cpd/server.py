# src/od_cpd/server.py
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .dbio import ro_conn, DBMissingError
from .export import write_csv, write_xlsx
from .primer import PRIMER
from .tools import lookup
from .tools.sql import run_sql_on, validate_select
from .tools.resolve import resolve_from
from .tools.inspect import get_project_schedule_from, get_project_budget_from
from .tools.schedule import (schedule_breakdown_from, schedule_changes_from,
                             delay_reason_stats_from)
from .tools.budget import budget_breakdown_from, budget_change_from
from .tools.ranking import rank_projects_from
from .tools.lifecycle import project_duration_stats_from
from .tools.portfolio import project_portfolio_from

mcp = FastMCP("od-cpd", instructions=PRIMER)


@mcp.tool()
def run_sql(query: str, output: str = "inline") -> dict:
    """Run a read-only SELECT against the local CPD DuckDB.

    output: 'inline' (default, rows capped) | 'csv' | 'xlsx' (writes a file, returns path).
    PREFER the typed tables: latest_project_state (1 row/PID, borough+boroughs,
    attributed_budget), schedule_history (PID x period), budget_history (snapshot rows
    at the (fms_id, managing_agency) x period grain), original_budget (adopted first
    budgets), lifetime_budget_variance (per-line lifetime, original_budget_source),
    schedule_budget_link (PID<->FMS edges), pid_funding (per-PID link rollup),
    cumulative_schedule_variance (per-PID lifetime days), fms_sponsor (fms_id -> owner),
    fms_location (line-level borough/community board), agency_rollup_by_period,
    category_dim, agency_dim, project_budget_fy, meta.
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
                return run_sql_on(con, query)
            q = validate_select(query)
            if output == "csv":
                path = write_csv(con, q)
            elif output == "xlsx":
                # provenance describes the full export; don't re-run the query
                prov = {"definition": "run_sql export (full result set)",
                        "reproduce_sql": q}
                path = write_xlsx(con, q, prov)
            else:
                return {"error": f"unknown output mode: {output}"}
            return {"file": str(path), "provenance": {"reproduce_sql": q}}
    except DBMissingError as e:
        return {"error": str(e)}


@mcp.tool()
def dataset_info() -> dict:
    """Per-dataset freshness, current period, row counts, and the key caveats."""
    try:
        with ro_conn() as con:
            return lookup.dataset_info_from(con)
    except DBMissingError as e:
        return {"error": str(e)}


@mcp.tool()
def list_agencies(contains: str | None = None) -> dict:
    """Agency dictionary with live CPD presence + schedule-executor flag."""
    try:
        with ro_conn() as con:
            return lookup.list_agencies_from(con, contains=contains)
    except DBMissingError as e:
        return {"error": str(e)}


@mcp.tool()
def list_categories() -> dict:
    """Program/facility categories (Library, Parks & Recreation, Sewer & Water, …)
    with budget-line counts and total budget. Use a category name as the `category`
    filter on rank_projects. Categories are derived from ten_year_plan_category +
    sponsor_agency + fms-id prefix — NOT managing_agency or project name."""
    try:
        with ro_conn() as con:
            return lookup.list_categories_from(con)
    except DBMissingError as e:
        return {"error": str(e)}


@mcp.tool()
def describe_field(field: str | None = None, dataset: str | None = None) -> dict:
    """Official field definitions (the NYC Open Data data dictionary): description,
    allowed values, primary/foreign key, limitations, notes. Filter by `field` (column
    name or display name) and/or `dataset` (RAW table name or socrata_id); omit both
    for the full dictionary."""
    try:
        with ro_conn() as con:
            return lookup.describe_field_from(con, field, dataset)
    except DBMissingError as e:
        return {"error": str(e)}


def _with_conn(fn, *args, **kwargs):
    try:
        with ro_conn() as con:
            return fn(con, *args, **kwargs)
    except DBMissingError as e:
        return {"error": str(e)}


@mcp.tool()
def resolve_project_reference(query: str) -> dict:
    """Resolve any project identifier (PID, FMS ID, name, partial) → schedule+budget
    matches bucketed by entity. Call this first for any named-project question."""
    return _with_conn(resolve_from, query)


@mcp.tool()
def get_project_schedule(pid: str) -> dict:
    """Schedule (PID): phase, lifecycle, signed variance, reason; lists linked budgets."""
    return _with_conn(get_project_schedule_from, pid)


@mcp.tool()
def get_project_budget(fms_id: str, managing_agency: str | None = None) -> dict:
    """Budget (FMS line): total, spend, variance; lists linked schedules. NB budget has
    no 'completed' state; spend%=100 ≠ done."""
    return _with_conn(get_project_budget_from, fms_id, managing_agency)


@mcp.tool()
def schedule_breakdown(group_by: str, metric: str = "count", statistic: str = "count",
                       period: str = "current", agency: str | None = None,
                       agency_role: str = "auto") -> dict:
    """Counts/averages of schedule metrics by agency/sponsor/borough/phase/category.
    `agency` scopes to one agency; `agency_role` ('auto'|'sponsor'|'managing') picks owner
    vs builder lens (auto: sponsor, except DDC/DCAS/EDC -> managing). Category grouping
    counts a PID once in EACH of its categories (non-additive). Report neutral, signed
    variance."""
    return _with_conn(schedule_breakdown_from, group_by, metric, statistic, period,
                      agency, agency_role)


@mcp.tool()
def schedule_changes(change_type: str, from_period: str, to_period: str,
                     agency: str | None = None, include_cancelled: bool = False,
                     agency_role: str = "auto") -> dict:
    """Newly completed (DR1) or newly delayed projects between two periods. `agency` scopes
    to one agency; `agency_role` ('auto'|'sponsor'|'managing') picks owner vs builder lens."""
    return _with_conn(schedule_changes_from, change_type, from_period, to_period,
                      agency, include_cancelled, agency_role)


@mcp.tool()
def delay_reason_stats(period: str = "current", agency: str | None = None,
                       scope: str = "current", agency_role: str = "auto") -> dict:
    """Distribution of reason-for-delay (only populated when variance>0). Defaults to current
    period; pass scope='all_history' for lifetime. `agency_role` ('auto'|'sponsor'|'managing')
    picks owner vs builder lens."""
    return _with_conn(delay_reason_stats_from, period, agency, scope, agency_role)


@mcp.tool()
def budget_breakdown(group_by: str = "managing_agency", metric: str = "total_budget",
                     period: str = "current", agency: str | None = None,
                     agency_role: str = "auto") -> dict:
    """Total budget / spend by managing_agency or category, deduped on (fms_id,
    managing_agency). Category is line-grain (additive). Optional `agency` scopes to one
    agency; `agency_role` ('auto'|'sponsor'|'managing') picks owner vs builder lens. For
    richer cuts use run_sql."""
    return _with_conn(budget_breakdown_from, group_by, metric, period, agency, agency_role)


@mcp.tool()
def budget_change(target: str, from_period: str, to_period: str,
                  metric: str = "total_budget", agency_role: str = "auto") -> dict:
    """Δ budget/spend for an agency ('agency:DEP') or FMS line ('fms:ABC') between two periods.
    For an agency target, `agency_role` ('auto'|'sponsor'|'managing') picks the lens; sponsor
    scope uses the latest-period owner set (as-of caveat in the result label)."""
    return _with_conn(budget_change_from, target, from_period, to_period, metric, agency_role)


@mcp.tool()
def rank_projects(entity: str, rank_by: str, n: int = 10, direction: str = "top",
                  min_total_budget: float | None = None, max_total_budget: float | None = None,
                  delayed_only: bool = False, category: str | None = None,
                  agency: str | None = None, agency_role: str = "auto") -> dict:
    """Rank schedules (entity='schedule', rows=PIDs) or budgets (entity='budget', rows=FMS lines).
    rank_by must be NATIVE to entity; the other domain is filter-only. Echoes ranked_entity.
    Budget rank_by: total_budget | spend_to_date | spend_pct | budget_variance
    (last-period delta) | cumulative_budget_change (latest - original budget).
    Optional `category` (see list_categories) filters to one program type, e.g. 'Library'.
    Optional `agency` scopes to one agency; `agency_role` ('auto'|'sponsor'|'managing') picks
    the lens — 'auto' uses the owner (sponsor) view, except DDC/DCAS/EDC default to builder
    (managing). Echoes agency_scope."""
    return _with_conn(rank_projects_from, entity, rank_by, n, direction,
                      min_total_budget, max_total_budget, delayed_only, category,
                      agency, agency_role)


@mcp.tool()
def project_duration_stats(from_milestone: str = "actual_design_start",
                           to_milestone: str = "actual_construction_end",
                           group_by: str | None = None) -> dict:
    """Duration distribution between two ACTUAL milestones (requires both dates).
    Optional group_by ('managing_agency'|'borough'|'lifecycle_status') returns per-group
    stats instead of the citywide block."""
    return _with_conn(project_duration_stats_from, from_milestone, to_milestone, group_by)


@mcp.tool()
def project_portfolio(category: str | None = None, borough: str | None = None,
                      community_board: str | None = None,
                      lifecycle_status: str | None = None,
                      agency: str | None = None, agency_role: str = "auto",
                      n: int = 50) -> dict:
    """Cross-section listing of projects (PIDs): filter by category (see
    list_categories), borough, community_board, lifecycle_status
    ('in_progress'|'completed'|'cancelled'), and/or agency (+agency_role lens);
    rows ordered by nearest completion date (NULLs last). Each row carries schedule
    state + attributed_budget; `summary` covers the FULL filtered set and reports
    BOTH budget bases (per-PID attributed vs deduped line_budget_total). Borough
    matches the PID's boroughs LIST, so multi-borough projects are found by any of
    their boroughs."""
    return _with_conn(project_portfolio_from, category, borough, community_board,
                      lifecycle_status, agency, agency_role, n)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
