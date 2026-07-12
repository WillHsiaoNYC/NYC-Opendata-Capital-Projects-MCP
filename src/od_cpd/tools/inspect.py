# src/od_cpd/tools/inspect.py
from __future__ import annotations

import duckdb
from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import interpolate_sql, mm_envelope, signed_metric, VARIANCE_ARTIFACT_DAYS


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
                row_count=1, reproduce_sql=interpolate_sql(
                    "SELECT * FROM latest_project_state WHERE pid = ?", [pid]))}


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


def get_project_history_from(con: duckdb.DuckDBPyConnection, pid: str | None = None,
                             fms_id: str | None = None,
                             managing_agency: str | None = None) -> dict:
    """Period-by-period snapshots for ONE project: schedule lens (pid) or budget
    lens (fms_id). Exactly one anchor id."""
    if (pid is None) == (fms_id is None):
        return {"error": "Provide exactly one of pid or fms_id."}
    if pid is not None:
        return _schedule_history_answer(con, pid)
    return _budget_history_answer(con, fms_id, managing_agency)


def _schedule_history_answer(con: duckdb.DuckDBPyConnection, pid: str) -> dict:
    sql = ("SELECT reporting_period, current_phase, lifecycle_status, "
           "forecast_completion, variance_day, reason_for_delay, "
           "completion_date, completion_date_type "
           "FROM schedule_history WHERE pid = ? ORDER BY reporting_period")
    rows = rows_as_dicts(con, sql, [pid])
    if not rows:
        return {"error": f"No schedule (PID) found for {pid}"}
    for r in rows:
        v = r.pop("variance_day")
        r["variance_days"] = signed_metric(v)
        if v is not None and abs(v) > VARIANCE_ARTIFACT_DAYS:
            # kept, not dropped: this is a detail listing, not a statistic
            r["variance_artifact"] = True
        r["forecast_completion"] = (str(r["forecast_completion"])
                                    if r["forecast_completion"] else None)
    cum = con.execute("SELECT cumulative_variance_days FROM cumulative_schedule_variance "
                      "WHERE pid = ?", [pid]).fetchone()
    last = rows[-1]
    current_state = {"reporting_period": last["reporting_period"],
                     "current_phase": last["current_phase"],
                     "lifecycle_status": last["lifecycle_status"],
                     "cumulative_variance_days": signed_metric(cum[0] if cum else None)}
    linked = rows_as_dicts(con,
        "SELECT DISTINCT fms_id, managing_agency FROM schedule_budget_link "
        "WHERE pid = ? QUALIFY reporting_period = max(reporting_period) OVER ()", [pid])
    env = mm_envelope(anchor_type="schedule", anchor_id=pid, linked=linked)
    return {"current_state": current_state, "periods": rows, **env,
            "provenance": provenance_block(
                definition="schedule_history rows for PID, period by period",
                scope={"pid": pid}, row_count=len(rows),
                reproduce_sql=interpolate_sql(sql, [pid]))}


def _budget_history_answer(con: duckdb.DuckDBPyConnection, fms_id: str,
                           managing_agency: str | None) -> dict:
    # lower(): FMS ids are stored uppercase but users type them as resolve accepted them.
    where = "lower(fms_id) = lower(?)" + (" AND managing_agency = ?" if managing_agency else "")
    params = [fms_id] + ([managing_agency] if managing_agency else [])
    sql = (f"SELECT fms_id, managing_agency, reporting_period, total_budget, "
           f"spend_to_date, spend_pct, budget_variance, budget_variance_pct "
           f"FROM budget_history WHERE {where} ORDER BY managing_agency, reporting_period")
    snap = rows_as_dicts(con, sql, params)
    orig = rows_as_dicts(con,
        f"SELECT fms_id, managing_agency, recorded_period, original_budget "
        f"FROM original_budget WHERE {where}", params)
    if not snap and not orig:
        return {"error": f"No budget (FMS line) found for {fms_id}"}
    orig_by_line = {(o["fms_id"], o["managing_agency"]): o for o in orig}
    keys: list[tuple] = []
    for r in snap + orig:
        k = (r["fms_id"], r["managing_agency"])
        if k not in keys:
            keys.append(k)
    lines = []
    for k in keys:
        periods = []
        for r in snap:
            if (r["fms_id"], r["managing_agency"]) != k:
                continue
            p = dict(r)
            p.pop("fms_id"); p.pop("managing_agency")
            p["budget_variance"] = signed_metric(p["budget_variance"], "budget")
            periods.append(p)
        o = orig_by_line.get(k)
        line = {"fms_id": k[0], "managing_agency": k[1],
                "original_budget": ({"amount": o["original_budget"],
                                     "recorded_period": o["recorded_period"],
                                     "note": "Adoption record (any calendar month), "
                                             "not a reporting snapshot."} if o else None),
                "periods": periods}
        if not periods:
            line["note"] = ("Adoption-only line: an original budget exists but no "
                            "reporting snapshot yet — normal for early allocations.")
        lines.append(line)
    linked = rows_as_dicts(con,
        f"SELECT DISTINCT pid, managing_agency FROM schedule_budget_link "
        f"WHERE {where} QUALIFY reporting_period = max(reporting_period) OVER ()", params)
    env = mm_envelope(anchor_type="budget", anchor_id=fms_id, linked=linked)
    result = {"lines": lines, **env,
              "provenance": provenance_block(
                  definition="budget_history rows per line, period by period "
                             "(+ original_budget header)",
                  scope={"fms_id": fms_id, "managing_agency": managing_agency},
                  row_count=len(snap), reproduce_sql=interpolate_sql(sql, params))}
    if len(lines) > 1:
        result["grain_note"] = ("This FMS id is held by multiple managing agencies — "
                                "each is a distinct budget line ((managing_agency, "
                                "fms_id) grain); never sum across lines.")
    return result
