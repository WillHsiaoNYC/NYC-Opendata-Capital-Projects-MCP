# src/od_cpd/tools/schedule.py
from __future__ import annotations

from ..config import CADENCE_MONTHS
from ..dbio import rows_as_dicts
from ..periods import is_cadence_period
from ..provenance import provenance_block
from ._common import (BOROUGH_GROUP_NOTE, CATEGORY_GROUP_NOTE, VARIANCE_ARTIFACT_DAYS,
                      direction_of, interpolate_sql, resolve_period, validate_choice)
from .agency_scope import resolve_agency_scope

_GROUPABLE = {"managing_agency", "sponsor_agency", "borough", "phase_norm",
              "lifecycle_status", "category"}

# statistic -> aggregate over variance_day. 'count' counts PIDs with a variance value
# (it is the registered default, so it must be a real statistic, not a fallback).
_VARIANCE_STATS = {
    "count": "count(variance_day)", "mean": "avg(variance_day)",
    "median": "median(variance_day)", "sum": "sum(variance_day)",
    "min": "min(variance_day)", "max": "max(variance_day)",
}


def schedule_breakdown_from(con, group_by, metric="count", statistic="count",
                            period="current", agency=None, agency_role="auto"):
    error = validate_choice(agency_role, {"auto", "sponsor", "managing"}, "agency_role")
    if error:
        return error
    if metric == "count" and statistic != "count":
        return {"error": "metric='count' requires statistic='count'; use "
                         "metric='schedule_variance' for day-valued statistics."}
    if group_by not in _GROUPABLE:
        return {"error": f"group_by must be one of {sorted(_GROUPABLE)}"}
    p, err = resolve_period(con, "schedule_history", period)
    if err:
        return err
    where = "reporting_period = ?"
    params = [p]
    scope = None
    if agency:
        scope = resolve_agency_scope(con, agency, agency_role, entity="schedule")
        if "error" in scope:
            return scope
        where += f" AND {scope['where']}"
    excluded_artifacts = None
    excluded_sql = None
    if metric == "count":
        agg = "count(*)"
    elif metric == "schedule_variance":
        agg = _VARIANCE_STATS.get(statistic)
        if agg is None:
            return {"error": f"statistic must be one of {sorted(_VARIANCE_STATS)}"}
        # Forecast-placeholder artifacts (raw values like −364,938 days) corrupt
        # day-valued aggregates — same guard as rank_projects, dropped rows counted.
        excluded_sql = (
            f"SELECT count(*) AS excluded_artifacts FROM schedule_history WHERE {where} "
            f"AND variance_day IS NOT NULL "
            f"AND variance_day NOT BETWEEN -{VARIANCE_ARTIFACT_DAYS} AND {VARIANCE_ARTIFACT_DAYS}")
        excluded_artifacts = con.execute(excluded_sql, params).fetchone()[0]
        where += (f" AND variance_day BETWEEN -{VARIANCE_ARTIFACT_DAYS} "
                  f"AND {VARIANCE_ARTIFACT_DAYS}")
    else:
        return {"error": "metric must be 'count' or 'schedule_variance'"}
    if group_by == "sponsor_agency":
        # Split composite sponsor strings (e.g. 'DOT, DPR') into atomic buckets so a
        # co-sponsored project is grouped under each owner, never under a 'DOT, DPR' key —
        # consistent with the atomic sponsor model used by fms_sponsor.
        src = "schedule_history CROSS JOIN unnest(string_split(sponsor_agency, ',')) AS _ss(atom)"
        grp = "trim(_ss.atom)"
        where += " AND trim(_ss.atom) <> ''"
    elif group_by == "category":
        # Count-in-each (owner ruling 2026-06-12): the DISTINCT (pid, period, category)
        # join gives a PID one row PER category — once within a category even when
        # several of its lines share it, once more in each further category.
        src = ("schedule_history JOIN "
               "(SELECT DISTINCT l.pid, l.reporting_period, c.category "
               " FROM schedule_budget_link l JOIN category_dim c USING (fms_id, managing_agency)) cat "
               "USING (pid, reporting_period)")
        grp = "cat.category"
    else:
        src, grp = "schedule_history", group_by
    sql = (f"SELECT {grp} AS {group_by}, {agg} AS value, count(*) AS n FROM {src} "
           f"WHERE {where} GROUP BY {grp} ORDER BY value DESC")
    groups = rows_as_dicts(con, sql, params)
    if metric == "schedule_variance" and statistic != "count":
        # a count is an unsigned magnitude — direction only applies to day-valued stats
        for g in groups:
            g["direction"] = direction_of(g["value"])
    result = {"groups": groups, "period": p, "metric": metric, "statistic": statistic,
              "label": (f"Most-recent reporting period ({p}). Variance is the per-period delta; "
                        "ask for cumulative if you want lifetime."),
              "provenance": provenance_block(
                  definition=f"{statistic} of {metric} by {group_by}",
                  scope={"period": p, "agency": agency, "agency_role": agency_role},
                  row_count=len(groups), reproduce_sql=interpolate_sql(sql, params),
                  components={"groups": interpolate_sql(sql, params),
                              **({"excluded_artifacts": interpolate_sql(excluded_sql, params)}
                                 if excluded_sql else {})})}
    if excluded_artifacts is not None:
        result["excluded_artifacts"] = excluded_artifacts
        if excluded_artifacts:
            result["label"] += (f" {excluded_artifacts} forecast-placeholder artifact(s) "
                                f"(|variance| > {VARIANCE_ARTIFACT_DAYS} days) excluded.")
    if group_by == "borough":
        result["label"] += " " + BOROUGH_GROUP_NOTE
    elif group_by == "category":
        result["label"] += " " + CATEGORY_GROUP_NOTE
    if scope is not None:
        result["agency_scope"] = scope["agency_scope"]
    return result


def delay_reason_stats_from(con, period="current", agency=None, scope="current",
                            agency_role="auto"):
    error = (validate_choice(scope, {"current", "all_history"}, "scope")
             or validate_choice(agency_role, {"auto", "sponsor", "managing"}, "agency_role"))
    if error:
        return error
    base_where = "variance_day > 0"
    params = []
    if scope != "all_history":
        p, err = resolve_period(con, "schedule_history", period)
        if err:
            return err
        base_where += " AND reporting_period = ?"; params.append(p)
    else:
        p = "all_history"
    ascope = None
    if agency:
        ascope = resolve_agency_scope(con, agency, agency_role, entity="schedule")
        if "error" in ascope:
            return ascope
        base_where += f" AND {ascope['where']}"
    sql = (f"SELECT reason_for_delay, count(*) AS n FROM schedule_history "
           f"WHERE {base_where} AND reason_for_delay IS NOT NULL "
           f"GROUP BY reason_for_delay ORDER BY n DESC")
    rows = rows_as_dicts(con, sql, params)
    coverage_sql = ("SELECT count(*) AS delayed_total, count(reason_for_delay) AS with_reason, "
                    "count(*) - count(reason_for_delay) AS without_reason "
                    f"FROM schedule_history WHERE {base_where}")
    coverage = rows_as_dicts(con, coverage_sql, params)[0]
    result = {"reasons": rows,
              "coverage": coverage,
              "scope": p,
              "label": (f"Delay reasons are populated only when variance_day>0. Scope: {p}. "
                        "coverage gives the denominator: delayed rows with vs without a reason."),
              "provenance": provenance_block(
                  definition="distribution of reason_for_delay (variance>0)",
                  scope={"period": p, "agency": agency, "agency_role": agency_role},
                  row_count=len(rows), reproduce_sql=interpolate_sql(sql, params),
                  components={"reasons": interpolate_sql(sql, params),
                              "coverage": interpolate_sql(coverage_sql, params)})}
    if ascope is not None:
        result["agency_scope"] = ascope["agency_scope"]
    return result


def schedule_changes_from(con, change_type, from_period=None, to_period=None, agency=None,
                          include_cancelled=False, agency_role="auto"):
    """change_type ∈ {'completed','delayed'} — both compare the two periods.

    'completed': PIDs not finished at ``from_period`` but completed/cancelled at
    ``to_period``. 'delayed': PIDs with positive variance at ``to_period`` that had
    no positive variance at ``from_period`` (absent then counts as not-delayed).
    A nonexistent period must error, not silently match nothing — an off-cadence
    ``from_period`` would otherwise report EVERY completed/delayed project as new.
    """
    error = (validate_choice(change_type, {"completed", "delayed"}, "change_type")
             or validate_choice(agency_role, {"auto", "sponsor", "managing"}, "agency_role"))
    if error:
        return error
    if not (isinstance(from_period, str) and isinstance(to_period, str)
            and is_cadence_period(from_period) and is_cadence_period(to_period)):
        return {"error": f"Periods must be YYYYMM ending in {'/'.join(CADENCE_MONTHS)} "
                         "(e.g. 202509); see dataset_info for available periods."}
    if change_type != "completed" and include_cancelled:
        return {"error": "include_cancelled applies only to change_type='completed'."}
    if from_period >= to_period:
        return {"error": "from_period must be earlier than to_period."}
    available = [r[0] for r in con.execute(
        "SELECT DISTINCT reporting_period FROM schedule_history ORDER BY reporting_period"
    ).fetchall()]
    if to_period not in available:
        return {"error": f"No schedule data at to_period {to_period}; "
                         f"available periods: {', '.join(available)}.",
                "available_periods": available}
    from_exists = from_period in available
    if not from_exists and from_period >= available[0]:
        return {"error": f"No schedule data at from_period {from_period}; this is an "
                         f"interior gap, not a pre-history baseline. Available periods: "
                         f"{', '.join(available)}.", "available_periods": available}
    ascope = None
    ag = ""
    if agency:
        ascope = resolve_agency_scope(con, agency, agency_role, entity="schedule", alias="t")
        if "error" in ascope:
            return ascope
        ag = f" AND {ascope['where']}"
    if change_type == "completed":
        excl = "" if include_cancelled else " AND t.lifecycle_status = 'completed'"
        sql = (f"SELECT t.pid, t.managing_agency, t.agency_project_name, t.lifecycle_status FROM schedule_history t "
               f"LEFT JOIN schedule_history f ON t.pid=f.pid AND f.reporting_period=? "
               f"WHERE t.reporting_period=? "
               f"AND (f.pid IS NULL OR f.lifecycle_status NOT IN ('completed','cancelled')) "
               f"AND t.lifecycle_status IN ('completed','cancelled'){excl}{ag}")
        params = [from_period, to_period]
    elif change_type == "delayed":
        sql = (f"SELECT t.pid, t.managing_agency, t.agency_project_name, t.variance_day FROM schedule_history t "
               f"LEFT JOIN schedule_history f ON t.pid=f.pid AND f.reporting_period=? "
               f"WHERE t.reporting_period=? AND t.variance_day>0 "
               f"AND (f.pid IS NULL OR f.variance_day IS NULL OR f.variance_day<=0){ag}")
        params = [from_period, to_period]
    else:
        return {"error": "change_type must be 'completed' or 'delayed'"}
    rows = rows_as_dicts(con, sql, params)
    result = {"changes": rows, "change_type": change_type,
              "from_period": from_period, "to_period": to_period,
              "provenance": provenance_block(definition=f"schedule {change_type} between periods",
                  scope={"from": from_period, "to": to_period, "agency": agency,
                         "agency_role": agency_role},
                  row_count=len(rows), reproduce_sql=interpolate_sql(sql, params))}
    if not from_exists:
        result["note"] = (f"from_period {from_period} predates the available data; "
                          "every project is treated as not-yet-completed/-delayed then.")
    if ascope is not None:
        result["agency_scope"] = ascope["agency_scope"]
    return result
