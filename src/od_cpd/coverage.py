"""Explicit reconciliation of dashboard schedules with source-native observations."""
from __future__ import annotations

from .dbio import rows_as_dicts, sql_literal

SCHEDULE_UNIVERSE = "dashboard_aligned"
SCHEDULE_COVERAGE_NOTE = (
    "Schedule statistics use the dashboard-aligned (fb86) PID/period population. "
    "The schedule source (95tx) may contain additional observations, including later "
    "ones; those are retained in source_schedule_history and do not enter the "
    "dashboard cumulative variance. Coverage counts compare the full available history.")


def schedule_coverage(con, pid: str | None = None) -> dict:
    if not con.execute("SELECT 1 FROM information_schema.tables "
                       "WHERE table_name='schedule_source_coverage'").fetchone():
        return {"universe": SCHEDULE_UNIVERSE, "available": False,
                "note": "Source reconciliation requires a rebuilt database."}
    if pid is not None:
        sql = f"SELECT * FROM schedule_source_coverage WHERE pid={sql_literal(pid)}"
        rows = rows_as_dicts(con, sql)
        result = rows[0] if rows else {"pid": pid, "source_rows": 0, "dashboard_rows": 0}
    else:
        sql = """SELECT coalesce(sum(source_rows), 0) AS source_rows,
                        coalesce(sum(dashboard_rows), 0) AS dashboard_rows,
                        coalesce(sum(matched_rows), 0) AS matched_rows,
                        coalesce(sum(source_only_rows), 0) AS source_only_rows,
                        coalesce(sum(dashboard_only_rows), 0) AS dashboard_only_rows,
                        count(*) FILTER (WHERE source_only_rows > 0) AS pids_with_omitted_source_rows,
                        count(*) FILTER (WHERE source_rows > 0 AND dashboard_rows = 0) AS source_only_pids
                 FROM schedule_source_coverage"""
        result = rows_as_dicts(con, sql)[0]
    return {**result, "universe": SCHEDULE_UNIVERSE, "available": True,
            "note": SCHEDULE_COVERAGE_NOTE, "reproduce_sql": sql}


def attach_schedule_coverage(con, result: dict, *, pid: str | None = None,
                             history: bool = False) -> dict:
    """Attach population evidence, retaining source-only PIDs for detail inspection."""
    coverage = schedule_coverage(con, pid)
    if "error" in result:
        if pid is None or not coverage.get("source_rows"):
            return result
        result = {"anchor": {"type": "schedule", "id": pid}, "linked_budgets": [],
                  "caveat": "This PID has source schedule observations but no dashboard-aligned row.",
                  "provenance": {"definition": "source-only schedule inspection", "reproduce_sql": None}}
        if history:
            result.update(current_state=None, periods=[])
        else:
            result["answer"] = None
    result["schedule_universe"] = SCHEDULE_UNIVERSE
    result["source_coverage"] = coverage
    prov = result.setdefault("provenance", {})
    prov.setdefault("scope", {})["schedule_universe"] = SCHEDULE_UNIVERSE
    if coverage.get("available"):
        prov.setdefault("components", {})["source_coverage"] = coverage["reproduce_sql"]
    if pid is not None and coverage.get("source_rows"):
        # A source-only later date/variance must be inspectable, not just counted.
        source_sql = ("SELECT * FROM source_schedule_history WHERE pid=" + sql_literal(pid)
                      + ("" if history else " AND NOT in_dashboard") + " ORDER BY reporting_period")
        result["source_periods"] = rows_as_dicts(con, source_sql)
        prov.setdefault("components", {})["source_periods"] = source_sql
    listed_rows = result.get("rows", []) + result.get("changes", [])
    pids = {r["pid"] for r in listed_rows if isinstance(r, dict) and r.get("pid")}
    if pids and coverage.get("available"):
        vals = ", ".join(sql_literal(p) for p in sorted(pids))
        by_pid = {r["pid"]: r for r in rows_as_dicts(con,
            f"SELECT pid, source_only_rows, source_latest_period FROM schedule_source_coverage WHERE pid IN ({vals})")}
        for row in listed_rows:
            if row.get("pid") in by_pid:
                row["source_coverage"] = by_pid[row["pid"]]
    return result
