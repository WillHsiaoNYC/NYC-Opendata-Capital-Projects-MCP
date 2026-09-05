# src/od_cpd/tools/lifecycle.py
from __future__ import annotations

from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import BOROUGH_GROUP_NOTE

_MILESTONES = ("actual_design_start", "actual_construction_end")
# sponsor_agency is excluded: it can be a composite string ('DOT, DPR') and would need
# atomization; use schedule_breakdown for sponsor cuts.
_GROUPABLE = {"managing_agency", "borough", "lifecycle_status"}

_NOTE = (
    "Duration statistics include only latest-known PIDs with both actual dates in "
    "forward order. Missing dates and negative intervals are excluded and counted; "
    "invalid_intervals retains the source dates. Most actual milestones may be "
    "suppressed; actual_construction_end is never suppressed.")


def project_duration_stats_from(con, from_milestone="actual_design_start",
                                to_milestone="actual_construction_end", group_by=None):
    if from_milestone not in _MILESTONES or to_milestone not in _MILESTONES:
        return {"error": f"milestones must be in {sorted(_MILESTONES)} (v1)"}
    if _MILESTONES.index(from_milestone) >= _MILESTONES.index(to_milestone):
        return {"error": "from_milestone must precede to_milestone; supported forward "
                         "order is actual_design_start to actual_construction_end."}
    if group_by is not None and group_by not in _GROUPABLE:
        return {"error": f"group_by must be one of {sorted(_GROUPABLE)} (or omitted)"}
    # schedule_history carries actual_design_start + actual_construction_end (typed DATE).
    # Use each PID's LATEST snapshot only, else a PID is counted once per period it appears in.
    gcol = f", {group_by}" if group_by else ""
    base = (f"WITH latest AS (SELECT pid{gcol}, {from_milestone} AS from_date, "
            f"{to_milestone} AS to_date "
            f"FROM schedule_history "
            f"QUALIFY row_number() OVER (PARTITION BY pid ORDER BY reporting_period DESC) = 1) "
            f"SELECT pid{gcol}, from_date, to_date, "
            f"datediff('day', from_date, to_date) AS days FROM latest")
    scope = {"from": from_milestone, "to": to_milestone, "group_by": group_by}
    # Keep all rows in the population; FILTER limits only the duration statistics.
    counts = ("count(*) AS population_total, "
              "count(*) FILTER (WHERE days >= 0) AS n_projects, "
              "count(*) FILTER (WHERE days IS NULL) AS excluded_missing_dates, "
              "count(*) FILTER (WHERE days < 0) AS excluded_invalid_order")
    stats = ", ".join(f"{func}(days) FILTER (WHERE days >= 0) AS {name}_days"
                      for func, name in [("avg", "mean"), ("median", "median"),
                                         ("min", "min"), ("max", "max")])
    summary_sql = f"SELECT {counts}, {stats} FROM ({base})"
    summary = rows_as_dicts(con, summary_sql)[0]
    quality = {key: summary.pop(key) for key in
               ("population_total", "n_projects", "excluded_missing_dates", "excluded_invalid_order")}
    invalid_sql = f"SELECT * FROM ({base}) WHERE days < 0 ORDER BY pid"
    invalid = rows_as_dicts(con, invalid_sql)
    for row in invalid:
        row["quality_flag"] = "negative_forward_interval"
    result = {**quality, "invalid_intervals": invalid, "note": _NOTE}
    components = {"summary": summary_sql, "invalid_intervals": invalid_sql}
    if group_by:
        sql = (f"SELECT {group_by}, {counts}, {stats} FROM ({base}) GROUP BY {group_by} "
               f"ORDER BY n_projects DESC, {group_by} NULLS LAST")
        groups = rows_as_dicts(con, sql)
        result.update(group_by=group_by, groups=groups)
        if group_by == "borough":
            result["note"] += " " + BOROUGH_GROUP_NOTE
        components["groups"] = sql
        row_count = len(groups)
    else:
        sql, row_count = summary_sql, 1
        result["stats"] = summary if quality["n_projects"] else None
    result["provenance"] = provenance_block(
        definition="duration between actual milestones, excluding invalid forward intervals",
        scope=scope, row_count=row_count, reproduce_sql=sql, components=components,
        excluded={"missing_dates": quality["excluded_missing_dates"],
                  "invalid_order": quality["excluded_invalid_order"]})
    return result
