# src/od_cpd/tools/ranking.py
from __future__ import annotations

import math

from ..dbio import rows_as_dicts, sql_literal
from ..provenance import provenance_block
from ._common import (VARIANCE_ARTIFACT_DAYS, budget_state_sql, category_pid_filter,
                      current_period, interpolate_sql, schedule_state_sql,
                      signed_metric, snapshot_presence_sql, validate_choice, validate_int)
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
                       category=None, agency=None, agency_role="auto",
                       population_scope="latest_known", category_scope="current"):
    error = (validate_choice(entity, {"schedule", "budget"}, "entity")
             or validate_choice(direction, {"top", "bottom"}, "direction")
             or validate_choice(population_scope, {"latest_known", "current"}, "population_scope")
             or validate_choice(category_scope, {"current", "all_history"}, "category_scope")
             or validate_choice(agency_role, {"auto", "sponsor", "managing"}, "agency_role")
             or validate_int(n, "n"))
    if error:
        return error
    if entity == "budget" and category_scope != "current":
        return {"error": "category_scope='all_history' applies only to schedule entities; "
                         "budget categories are assigned directly to each line."}
    for name, value in (("min_total_budget", min_total_budget), ("max_total_budget", max_total_budget)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value)):
            return {"error": f"{name} must be a finite number."}
    if (min_total_budget is not None and max_total_budget is not None
            and min_total_budget > max_total_budget):
        return {"error": "min_total_budget must not exceed max_total_budget."}
    metrics = _SCHEDULE_METRICS if entity == "schedule" else _BUDGET_METRICS
    error = validate_choice(rank_by, metrics, f"rank_by for entity={entity}")
    if error:
        return error
    order = "DESC" if direction == "top" else "ASC"
    period = current_period(con, f"{entity}_history")
    presence = snapshot_presence_sql(entity, period)
    extra_components = {}
    source_periods = {}
    scope = None
    if agency is not None:
        scope = resolve_agency_scope(con, agency, agency_role,
                                     entity=entity, alias="s")
        if "error" in scope:
            return scope
    if entity == "schedule":
        src = f"({schedule_state_sql(population_scope, period)}) s"
        if rank_by == "cumulative_variance_days":
            cumulative = "SELECT * FROM cumulative_schedule_variance"
            if population_scope == "current":
                cumulative = ("SELECT pid, sum(variance_day) AS cumulative_variance_days "
                              "FROM schedule_history WHERE reporting_period <= " + sql_literal(period) +
                              f" AND variance_day BETWEEN -{VARIANCE_ARTIFACT_DAYS} "
                              f"AND {VARIANCE_ARTIFACT_DAYS} GROUP BY pid")
            src += f" LEFT JOIN ({cumulative}) c USING (pid)"
        metric_expr = "c.cumulative_variance_days" if rank_by == "cumulative_variance_days" else f"s.{rank_by}"
        where = [f"{metric_expr} IS NOT NULL",
                 f"{metric_expr} BETWEEN -{VARIANCE_ARTIFACT_DAYS} AND {VARIANCE_ARTIFACT_DAYS}"]
        params = []
        if min_total_budget is not None:
            where.append("s.attributed_budget >= ?"); params.append(min_total_budget)
        if max_total_budget is not None:
            where.append("s.attributed_budget <= ?"); params.append(max_total_budget)
        if delayed_only:
            where.append("s.period_variance_days > 0")
        if category is not None:
            where.append(category_pid_filter("s", category_scope))
            params.append(category)
        if scope is not None:
            where.append(scope["where"])
        sql = (f"SELECT s.pid, s.agency_project_name, s.managing_agency, s.attributed_budget, "
               f"s.forecast_past_due, "
               f"{metric_expr} AS metric, s.reporting_period, "
               f"{presence} AS present_in_current_snapshot FROM {src} WHERE " + " AND ".join(where) +
               f" ORDER BY metric {order}, s.pid LIMIT {n}")
        rows = rows_as_dicts(con, sql, params)
        for r in rows:
            r[rank_by] = signed_metric(r.pop("metric"))
    else:
        col, signed = _BUDGET_METRICS[rank_by]
        # Schedule and budget publication completeness are resolved independently.
        # A complete budget snapshot must not promote partial schedule ownership.
        needs_schedules = delayed_only or (scope is not None and scope["agency_scope"]["role"] == "sponsor")
        schedule_period = (current_period(con, "schedule_history")
                           if population_scope == "current" and needs_schedules else None)
        selected_schedules = schedule_state_sql(population_scope, schedule_period)
        sponsor_sql = None
        if scope is not None and scope["agency_scope"]["role"] == "sponsor":
            source_periods = {
                "ownership_basis": ("selected_complete_schedule_snapshot"
                                    if population_scope == "current" else "latest_known_per_pid"),
                "ownership_reporting_period": schedule_period,
            }
            scope["agency_scope"].update(source_periods)
            if population_scope == "current":
                sponsor_sql = (
                    "SELECT DISTINCT b.fms_id, b.managing_agency, trim(atom) AS sponsor_agency "
                    f"FROM ({selected_schedules}) state "
                    "CROSS JOIN unnest(state.linked_budgets) AS _l(b) "
                    "CROSS JOIN unnest(string_split(state.sponsor_agency, ',')) AS u(atom) "
                    "WHERE trim(atom) <> ''")
                owner_scope = resolve_agency_scope(con, agency, "sponsor", entity="schedule", alias="owner")
                scope["where"] = (
                    "(s.managing_agency, s.fms_id) IN (SELECT owner.managing_agency, owner.fms_id "
                    f"FROM ({sponsor_sql}) owner WHERE {owner_scope['where']})")
                scope["agency_scope"]["note"] += (
                    f" Ownership uses the selected complete schedule snapshot ({schedule_period or 'unavailable'}); "
                    f"budget values use the independently selected budget snapshot ({period or 'unavailable'}).")
        where = [f"s.{col} IS NOT NULL"]
        params = []
        if min_total_budget is not None:
            where.append("s.latest_budget >= ?"); params.append(min_total_budget)
        if max_total_budget is not None:
            where.append("s.latest_budget <= ?"); params.append(max_total_budget)
        if delayed_only:
            # Each PID's own latest funding set; a shared line still ranks once.
            if population_scope == "current":
                source_periods["schedule_filter_reporting_period"] = schedule_period
            where.append("(s.managing_agency, s.fms_id) IN ("
                         f"SELECT b.managing_agency, b.fms_id FROM ({selected_schedules}) delayed "
                         "CROSS JOIN unnest(delayed.linked_budgets) AS _l(b) "
                         "WHERE delayed.period_variance_days > 0)")
        if category is not None:
            where.append("(s.managing_agency, s.fms_id) IN ("
                         "SELECT managing_agency, fms_id FROM category_dim WHERE category = ?)")
            params.append(category)
        if scope is not None:
            where.append(scope["where"])
        names = "SELECT fms_id, managing_agency, fms_project_name FROM fms_location"
        if population_scope == "current":
            names = ("SELECT fms_id, managing_agency, first(fms_project_name "
                     "ORDER BY reporting_period DESC, fms_project_name) FILTER "
                     "(WHERE nullif(trim(fms_project_name), '') IS NOT NULL) AS fms_project_name "
                     f"FROM raw_project_detail WHERE reporting_period <= {sql_literal(period)} "
                     "AND right(reporting_period, 2) IN ('01', '05', '09') "
                     "GROUP BY fms_id, managing_agency")
        sql = (f"SELECT s.fms_id, s.managing_agency, loc.fms_project_name, s.{col} AS metric, "
               f"s.reporting_period, {presence} AS present_in_current_snapshot "
               f"FROM ({budget_state_sql(population_scope, period)}) s "
               f"LEFT JOIN ({names}) loc USING (fms_id, managing_agency) "
               f"WHERE " + " AND ".join(where) +
               f" ORDER BY metric {order}, s.fms_id, s.managing_agency LIMIT {n}")
        rows = rows_as_dicts(con, sql, params)
        for r in rows:
            v = r.pop("metric")
            r[rank_by] = signed_metric(v, "budget") if signed else v
        if scope is not None and scope["agency_scope"]["role"] == "sponsor" and rows:
            # Attach each line's FULL owner set (a line can be co-sponsored, e.g.
            # BBJ-Q → DOC+DEP), not just the queried agency — so co-ownership is visible
            # and the "don't sum across agencies" caveat is legible per row.
            if sponsor_sql is not None:
                keys = ", ".join(f"({sql_literal(r['managing_agency'])}, {sql_literal(r['fms_id'])})"
                                 for r in rows)
                owner_sql = ("SELECT fms_id, managing_agency, "
                             "list(DISTINCT sponsor_agency ORDER BY sponsor_agency) AS sponsor_agencies "
                             f"FROM ({sponsor_sql}) WHERE (managing_agency, fms_id) IN (VALUES {keys}) "
                             "GROUP BY fms_id, managing_agency")
                owners = {(fid, manager): names for fid, manager, names in con.execute(owner_sql).fetchall()}
                for r in rows:
                    r["sponsor_agencies"] = owners.get((r["fms_id"], r["managing_agency"]), [])
            else:
                in_ids = ", ".join(sql_literal(r["fms_id"]) for r in rows)
                owner_sql = ("SELECT fms_id, list(DISTINCT sponsor_agency ORDER BY sponsor_agency) "
                             "AS sponsor_agencies FROM fms_sponsor "
                             f"WHERE fms_id IN ({in_ids}) GROUP BY fms_id")
                owners = dict(con.execute(owner_sql).fetchall())
                for r in rows:
                    r["sponsor_agencies"] = owners.get(r["fms_id"], [])
            extra_components["sponsor_agencies"] = owner_sql
    result = {"ranked_entity": entity, "rank_by": rank_by, "rows": rows,
              "population_scope": population_scope, "current_period": period,
              "category_scope": category_scope,
              **source_periods,
              "label": (("Schedule variance basis = cumulative across available history "
                          "through the selected state period."
                         if rank_by == "cumulative_variance_days" else
                         "Schedule variance basis = the row's reported period.")
                        if entity == "schedule" else
                        "budget_variance = last-period (source LAG) delta; "
                        "cumulative_budget_change = latest - original budget."),
              "provenance": provenance_block(definition=f"top {n} {entity} by {rank_by} ({direction})",
                  scope={"entity": entity, "rank_by": rank_by,
                         "population_scope": population_scope, "current_period": period,
                         "category_scope": category_scope,
                         **source_periods,
                         "filters": {"min_total_budget": min_total_budget,
                                     "max_total_budget": max_total_budget,
                                     "delayed_only": delayed_only, "category": category,
                                     "agency": agency, "agency_role": agency_role}},
                  row_count=len(rows), reproduce_sql=interpolate_sql(sql, params),
                  components={"rows": interpolate_sql(sql, params), **extra_components})}
    if scope is not None:
        result["agency_scope"] = scope["agency_scope"]
    return result
