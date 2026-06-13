# src/od_cpd/tools/inspect.py
from __future__ import annotations

import duckdb
from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import interpolate_sql, mm_envelope, signed_metric


def get_project_schedule_from(con: duckdb.DuckDBPyConnection, pid: str) -> dict:
    state = rows_as_dicts(con, "SELECT * FROM latest_project_state WHERE pid = ?", [pid])
    if not state:
        return {"error": f"No schedule (PID) found for {pid}"}
    s = state[0]
    # Use the link table's OWN latest period for this PID: a PID's latest schedule_history
    # snapshot can be a period where its fb86 row had a null fms_id (no edge), which would
    # wrongly yield zero linked budgets. The link table's max period always has fms_id.
    linked = rows_as_dicts(con,
        "SELECT DISTINCT fms_id, managing_agency FROM schedule_budget_link "
        "WHERE pid = ? QUALIFY reporting_period = max(reporting_period) OVER ()", [pid])
    answer = {
        "pid": pid, "agency": s["managing_agency"], "sponsor_agency": s["sponsor_agency"],
        "borough": s["borough"], "boroughs": s["boroughs"],
        "phase": s["current_phase"],
        "lifecycle_status": s["lifecycle_status"],
        "period_variance_days": signed_metric(s["period_variance_days"]),
        "reason_for_delay": s["reason_for_delay"],
        "forecast_completion": str(s["forecast_completion"]) if s["forecast_completion"] else None,
        "attributed_budget": s["attributed_budget"],
    }
    env = mm_envelope(anchor_type="schedule", anchor_id=pid, linked=linked)
    return {"answer": answer, **env,
            "provenance": provenance_block(
                definition="latest_project_state row for PID", scope={"pid": pid},
                row_count=1, reproduce_sql=f"SELECT * FROM latest_project_state WHERE pid='{pid}'")}


def get_project_budget_from(con: duckdb.DuckDBPyConnection, fms_id: str,
                            managing_agency: str | None = None) -> dict:
    # lower(): FMS ids are stored uppercase but users type them as resolve accepted them.
    where = "lower(fms_id) = lower(?)" + (" AND managing_agency = ?" if managing_agency else "")
    params = [fms_id] + ([managing_agency] if managing_agency else [])
    bud = rows_as_dicts(con, f"SELECT * FROM lifetime_budget_variance WHERE {where}", params)
    if not bud:
        return {"error": f"No budget (FMS line) found for {fms_id}"}
    # Mirror the schedule side: only links from this line's latest link period are
    # CURRENT counterparts; all-history links resurrect PIDs the line no longer funds.
    linked = rows_as_dicts(con,
        f"SELECT DISTINCT pid, managing_agency FROM schedule_budget_link "
        f"WHERE {where} QUALIFY reporting_period = max(reporting_period) OVER ()", params)
    env = mm_envelope(anchor_type="budget", anchor_id=fms_id, linked=linked)
    return {"answer": bud, **env,
            "provenance": provenance_block(
                definition="lifetime_budget_variance row(s) for FMS line",
                scope={"fms_id": fms_id, "managing_agency": managing_agency},
                row_count=len(bud), reproduce_sql=interpolate_sql(
                    f"SELECT * FROM lifetime_budget_variance WHERE {where}", params))}
