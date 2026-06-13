# src/od_cpd/tools/portfolio.py
from __future__ import annotations

from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import (CATEGORY_GROUP_NOTE, category_pid_filter, interpolate_sql,
                      signed_metric)
from .agency_scope import resolve_agency_scope

_LIFECYCLES = {"in_progress", "completed", "cancelled"}
_ROW_CAP = 500

_BASES_NOTE = (
    "Each row's attributed_budget sums the PID's OWN current funding-line commitments "
    "(a shared line counts fully on every PID it funds); summary.line_budget_total "
    "dedups the underlying (fms_id, managing_agency) lines once across the whole "
    "filtered set — the two totals legitimately differ when lines are shared. "
    "completion_date is a forecast unless completion_date_type='Actual'.")


def project_portfolio_from(con, category=None, borough=None, community_board=None,
                           lifecycle_status=None, agency=None, agency_role="auto",
                           n=50):
    """PID-grain cross-section: filter, then list by nearest completion (NULLs last).

    Summary aggregates cover the FULL filtered set, not just the returned rows.
    Borough matches the PID's line-derived boroughs LIST (plus the scalar, so
    'Multiple'/'Citywide' work too); community_board matches the line level.
    """
    if lifecycle_status is not None and lifecycle_status not in _LIFECYCLES:
        return {"error": f"lifecycle_status must be one of {sorted(_LIFECYCLES)}"}
    n = max(1, min(int(n), _ROW_CAP))
    where, params = [], []
    if category is not None:
        where.append(category_pid_filter("s"))
        params.append(category)
    if borough is not None:
        where.append("(list_contains(s.boroughs, ?) OR s.borough = ?)")
        params += [borough, borough]
    if community_board is not None:
        where.append("s.pid IN (SELECT l.pid FROM schedule_budget_link l "
                     "JOIN fms_location fl USING (fms_id, managing_agency) "
                     "WHERE fl.community_board = ?)")
        params.append(community_board)
    if lifecycle_status is not None:
        where.append("s.lifecycle_status = ?")
        params.append(lifecycle_status)
    scope = None
    if agency is not None:
        scope = resolve_agency_scope(con, agency, agency_role, entity="schedule", alias="s")
        if "error" in scope:
            return scope
        where.append(scope["where"])
    cond = " AND ".join(where) if where else "TRUE"

    row_sql = (
        f"SELECT s.pid, s.agency_project_name, s.managing_agency, s.sponsor_agency, "
        f"s.borough, s.boroughs, s.lifecycle_status, s.current_phase, "
        f"s.completion_date, s.completion_date_type, s.period_variance_days, "
        f"s.attributed_budget "
        f"FROM latest_project_state s "
        f"WHERE {cond} "
        f"ORDER BY s.completion_date IS NULL, s.completion_date, s.pid LIMIT {n}")
    rows = rows_as_dicts(con, row_sql, params)
    for r in rows:
        r["period_variance_days"] = signed_metric(r["period_variance_days"])

    summary = rows_as_dicts(con, f"""
        SELECT count(*) AS n_projects,
               sum(s.attributed_budget) AS attributed_budget_total,
               count(*) FILTER (WHERE s.completion_date IS NOT NULL)
                   AS n_with_completion_date,
               count(*) FILTER (WHERE s.period_variance_days > 0)
                   AS n_delayed_this_period
        FROM latest_project_state s WHERE {cond}""", params)[0]
    # Dedup the filtered set's funding lines before totaling — a line shared by
    # several matching PIDs must count once here (the per-row attribution above
    # is the count-in-each view; this is the cash view).
    summary["line_budget_total"] = con.execute(f"""
        SELECT sum(v.latest_budget) FROM lifetime_budget_variance v
        JOIN (SELECT DISTINCT b.fms_id, b.managing_agency
              FROM latest_project_state s
              CROSS JOIN unnest(s.linked_budgets) AS _l(b)
              WHERE {cond}) d
          ON v.fms_id = d.fms_id AND v.managing_agency = d.managing_agency""",
        params).fetchone()[0]

    notes = [_BASES_NOTE,
             "Row order is fixed to nearest completion; for largest/most-delayed "
             "rankings use rank_projects instead."]
    if category is not None:
        notes.append(CATEGORY_GROUP_NOTE)
    result = {
        "rows": rows,
        "truncated": summary["n_projects"] > len(rows),
        "summary": summary,
        "notes": notes,
        "provenance": provenance_block(
            definition="portfolio cross-section (latest state per PID)",
            scope={"category": category, "borough": borough,
                   "community_board": community_board,
                   "lifecycle_status": lifecycle_status,
                   "agency": agency, "agency_role": agency_role, "n": n},
            row_count=len(rows), reproduce_sql=interpolate_sql(row_sql, params)),
    }
    if scope is not None:
        result["agency_scope"] = scope["agency_scope"]
    return result
