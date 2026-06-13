# src/od_cpd/tools/lifecycle.py
from __future__ import annotations

from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import BOROUGH_GROUP_NOTE

_MILESTONES = {"actual_design_start", "actual_construction_end"}
# sponsor_agency is excluded: it can be a composite string ('DOT, DPR') and would need
# atomization; use schedule_breakdown for sponsor cuts.
_GROUPABLE = {"managing_agency", "borough", "lifecycle_status"}

_NOTE = "Only projects with BOTH actual dates are counted; suppression hits forecasts not actuals."


def project_duration_stats_from(con, from_milestone="actual_design_start",
                                to_milestone="actual_construction_end", group_by=None):
    if from_milestone not in _MILESTONES or to_milestone not in _MILESTONES:
        return {"error": f"milestones must be in {sorted(_MILESTONES)} (v1)"}
    if group_by is not None and group_by not in _GROUPABLE:
        return {"error": f"group_by must be one of {sorted(_GROUPABLE)} (or omitted)"}
    # schedule_history carries actual_design_start + actual_construction_end (typed DATE).
    # Use each PID's LATEST snapshot only, else a PID is counted once per period it appears in.
    gcol = f", {group_by}" if group_by else ""
    base = (f"WITH latest AS (SELECT pid{gcol}, {from_milestone} AS d_from, {to_milestone} AS d_to "
            f"FROM schedule_history "
            f"QUALIFY row_number() OVER (PARTITION BY pid ORDER BY reporting_period DESC) = 1) "
            f"SELECT pid{gcol}, datediff('day', d_from, d_to) AS days FROM latest "
            f"WHERE d_from IS NOT NULL AND d_to IS NOT NULL")
    total = con.execute("SELECT count(DISTINCT pid) FROM schedule_history").fetchone()[0]
    scope = {"from": from_milestone, "to": to_milestone, "group_by": group_by}
    # One aggregate engine for both shapes — grouped is the same SELECT plus GROUP BY.
    agg = ("count(*) AS n_projects, avg(days) AS mean_days, median(days) AS median_days, "
           "min(days) AS min_days, max(days) AS max_days")
    if group_by:
        sql = (f"SELECT {group_by}, {agg} FROM ({base}) GROUP BY {group_by} "
               f"ORDER BY n_projects DESC")
        groups = rows_as_dicts(con, sql)
        n = sum(g["n_projects"] for g in groups)
        note = _NOTE + (" " + BOROUGH_GROUP_NOTE if group_by == "borough" else "")
        return {"n_projects": n, "excluded_missing_dates": total - n,
                "group_by": group_by, "groups": groups, "note": note,
                "provenance": provenance_block(
                    definition=f"duration between actual milestones by {group_by}",
                    scope=scope, row_count=len(groups), reproduce_sql=sql)}
    sql = f"SELECT {agg} FROM ({base})"
    stats = rows_as_dicts(con, sql)[0]
    n = stats.pop("n_projects")
    if n == 0:
        return {"n_projects": 0, "excluded_missing_dates": total, "stats": None,
                "provenance": provenance_block(definition="duration between actual milestones",
                    scope=scope, row_count=0, reproduce_sql=sql)}
    return {"n_projects": n, "excluded_missing_dates": total - n,
            "stats": stats, "note": _NOTE,
            "provenance": provenance_block(definition="duration between actual milestones",
                scope=scope, row_count=n, reproduce_sql=sql)}
