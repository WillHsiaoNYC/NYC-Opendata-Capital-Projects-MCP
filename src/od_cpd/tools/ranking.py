# src/od_cpd/tools/ranking.py
from __future__ import annotations

from ..dbio import rows_as_dicts, sql_literal
from ..provenance import provenance_block
from ._common import category_pid_filter, interpolate_sql, signed_metric
from .agency_scope import resolve_agency_scope

_SCHEDULE_METRICS = {"period_variance_days", "cumulative_variance_days"}
# metric name -> (column, signed). Signed metrics return a {value, direction}
# envelope; declaring it here means a new metric can't silently miss the envelope.
_BUDGET_METRICS = {
    "total_budget": ("latest_budget", False),
    "spend_to_date": ("spend_to_date", False),
    "spend_pct": ("spend_pct", False),
    "budget_variance": ("budget_variance", True),
    "cumulative_budget_change": ("cumulative_budget_change", True),
}


def rank_projects_from(con, entity, rank_by, n=10, direction="top",
                       min_total_budget=None, max_total_budget=None, delayed_only=False,
                       category=None, agency=None, agency_role="auto"):
    order = "DESC" if direction == "top" else "ASC"
    n = max(1, int(n))
    scope = None
    if agency is not None:
        scope = resolve_agency_scope(con, agency, agency_role,
                                     entity=entity, alias=("s" if entity == "schedule" else ""))
        if "error" in scope:
            return scope
    if entity == "schedule":
        if rank_by not in _SCHEDULE_METRICS:
            return {"error": f"rank_by for entity=schedule must be in {sorted(_SCHEDULE_METRICS)}"}
        src = ("latest_project_state s LEFT JOIN cumulative_schedule_variance c USING (pid)"
               if rank_by == "cumulative_variance_days" else "latest_project_state s")
        metric_expr = "c.cumulative_variance_days" if rank_by == "cumulative_variance_days" else f"s.{rank_by}"
        where = [f"{metric_expr} IS NOT NULL", f"{metric_expr} BETWEEN -36500 AND 36500"]
        params = []
        if min_total_budget is not None:
            where.append("s.attributed_budget >= ?"); params.append(min_total_budget)
        if max_total_budget is not None:
            where.append("s.attributed_budget <= ?"); params.append(max_total_budget)
        if delayed_only:
            where.append("s.period_variance_days > 0")
        if category is not None:
            where.append(category_pid_filter("s"))
            params.append(category)
        if scope is not None:
            where.append(scope["where"])
        sql = (f"SELECT s.pid, s.managing_agency, s.attributed_budget, "
               f"{metric_expr} AS metric FROM {src} WHERE " + " AND ".join(where) +
               f" ORDER BY metric {order} LIMIT {int(n)}")
        rows = rows_as_dicts(con, sql, params)
        for r in rows:
            r[rank_by] = signed_metric(r.pop("metric"))
    elif entity == "budget":
        if rank_by not in _BUDGET_METRICS:
            return {"error": f"rank_by for entity=budget must be in {sorted(_BUDGET_METRICS)}"}
        col, signed = _BUDGET_METRICS[rank_by]
        where = [f"{col} IS NOT NULL"]
        params = []
        if min_total_budget is not None:
            where.append("latest_budget >= ?"); params.append(min_total_budget)
        if max_total_budget is not None:
            where.append("latest_budget <= ?"); params.append(max_total_budget)
        if delayed_only:
            where.append("fms_id IN (SELECT DISTINCT fms_id FROM schedule_budget_link l "
                         "JOIN latest_project_state s USING (pid) WHERE s.period_variance_days > 0)")
        if category is not None:
            where.append("fms_id IN (SELECT fms_id FROM category_dim WHERE category = ?)")
            params.append(category)
        if scope is not None:
            where.append(scope["where"])
        sql = (f"SELECT fms_id, managing_agency, {col} AS metric FROM lifetime_budget_variance "
               f"WHERE " + " AND ".join(where) + f" ORDER BY metric {order} LIMIT {int(n)}")
        rows = rows_as_dicts(con, sql, params)
        for r in rows:
            v = r.pop("metric")
            r[rank_by] = signed_metric(v, "budget") if signed else v
        if scope is not None and scope["agency_scope"]["role"] == "sponsor" and rows:
            # Attach each line's FULL owner set (a line can be co-sponsored, e.g.
            # BBJ-Q → DOC+DEP), not just the queried agency — so co-ownership is visible
            # and the "don't sum across agencies" caveat is legible per row.
            in_ids = ", ".join(sql_literal(r["fms_id"]) for r in rows)
            owners = {fid: sorted(s) for fid, s in con.execute(
                f"SELECT fms_id, list(DISTINCT sponsor_agency) FROM fms_sponsor "
                f"WHERE fms_id IN ({in_ids}) GROUP BY fms_id").fetchall()}
            for r in rows:
                r["sponsor_agencies"] = owners.get(r["fms_id"], [])
    else:
        return {"error": "entity must be 'schedule' or 'budget'"}
    result = {"ranked_entity": entity, "rank_by": rank_by, "rows": rows,
              "label": ("Schedule variance basis = most-recent period; ask for cumulative."
                        if entity == "schedule" else
                        "budget_variance = last-period (source LAG) delta; "
                        "cumulative_budget_change = latest - original budget."),
              "provenance": provenance_block(definition=f"top {n} {entity} by {rank_by} ({direction})",
                  scope={"entity": entity, "rank_by": rank_by,
                         "filters": {"min_total_budget": min_total_budget,
                                     "max_total_budget": max_total_budget,
                                     "delayed_only": delayed_only, "category": category,
                                     "agency": agency, "agency_role": agency_role}},
                  row_count=len(rows), reproduce_sql=interpolate_sql(sql, params))}
    if scope is not None:
        result["agency_scope"] = scope["agency_scope"]
    return result
