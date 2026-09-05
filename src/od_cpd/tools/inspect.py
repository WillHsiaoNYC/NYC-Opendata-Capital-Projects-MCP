# src/od_cpd/tools/inspect.py
from __future__ import annotations

import duckdb
from ..coverage import attach_schedule_coverage
from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import (current_pid_links_sql, interpolate_sql, mm_envelope,
                      signed_metric, VARIANCE_ARTIFACT_DAYS)


def _current_links_sql(*, where: str, select: str, anchor_type: str) -> str:
    # A project's CURRENT counterparts are the link edges at the link table's OWN latest
    # period. A PID's latest schedule_history snapshot can fall in a period where its fb86
    # row had a null fms_id (no edge), which would wrongly yield zero linked budgets — the
    # link table's max period always has fms_id. The budget side mirrors this: all-history
    # links would resurrect PIDs the line no longer funds.
    if anchor_type == "schedule":
        return f"SELECT DISTINCT {select} FROM ({current_pid_links_sql()}) WHERE {where}"
    return (f"SELECT DISTINCT {select} FROM schedule_budget_link WHERE {where} "
            "QUALIFY reporting_period = max(reporting_period) "
            "OVER (PARTITION BY fms_id, managing_agency)")


def _fms_line_filter(fms_id: str, managing_agency: str | None) -> tuple[str, list]:
    # lower(): FMS ids are stored uppercase but users type them as resolve accepted them.
    where = "lower(fms_id) = lower(?)" + (" AND managing_agency = ?" if managing_agency else "")
    return where, [fms_id] + ([managing_agency] if managing_agency else [])


def get_project_schedule_from(con: duckdb.DuckDBPyConnection, pid: str) -> dict:
    state_sql = "SELECT * FROM latest_project_state WHERE pid = ?"
    state = rows_as_dicts(con, state_sql, [pid])
    if not state:
        return attach_schedule_coverage(con, {"error": f"No schedule (PID) found for {pid}"}, pid=pid)
    s = state[0]
    link_sql = _current_links_sql(where="pid = ?", select="fms_id, managing_agency",
                                  anchor_type="schedule")
    linked = rows_as_dicts(con, link_sql, [pid])
    answer = {
        "pid": pid, "agency": s["managing_agency"], "sponsor_agency": s["sponsor_agency"],
        "reporting_period": s["reporting_period"],
        "borough": s["borough"], "boroughs": s["boroughs"],
        "phase": s["current_phase"],
        "lifecycle_status": s["lifecycle_status"],
        "period_variance_days": signed_metric(s["period_variance_days"]),
        "reason_for_delay": s["reason_for_delay"],
        "forecast_completion": str(s["forecast_completion"]) if s["forecast_completion"] else None,
        "forecast_past_due": s["forecast_past_due"],
        "attributed_budget": s["attributed_budget"],
    }
    env = mm_envelope(anchor_type="schedule", anchor_id=pid, linked=linked)
    result = {"answer": answer, **env,
            "provenance": provenance_block(
                definition="latest_project_state row for PID", scope={"pid": pid},
                row_count=1, reproduce_sql=interpolate_sql(state_sql, [pid]),
                components={"current_state": interpolate_sql(state_sql, [pid]),
                            "linked_budgets": interpolate_sql(link_sql, [pid])})}
    return attach_schedule_coverage(con, result, pid=pid)


def get_project_budget_from(con: duckdb.DuckDBPyConnection, fms_id: str,
                            managing_agency: str | None = None) -> dict:
    where, params = _fms_line_filter(fms_id, managing_agency)
    budget_sql = f"SELECT * FROM lifetime_budget_variance WHERE {where}"
    bud = rows_as_dicts(con, budget_sql, params)
    if not bud:
        return {"error": f"No budget (FMS line) found for {fms_id}"}
    link_sql = _current_links_sql(where=where, select="pid, managing_agency",
                                  anchor_type="budget")
    linked = rows_as_dicts(con, link_sql, params)
    env = mm_envelope(anchor_type="budget", anchor_id=fms_id, linked=linked)
    return {"answer": bud, **env,
            "provenance": provenance_block(
                definition="lifetime_budget_variance row(s) for FMS line",
                scope={"fms_id": fms_id, "managing_agency": managing_agency},
                row_count=len(bud), reproduce_sql=interpolate_sql(budget_sql, params),
                components={"budget_lines": interpolate_sql(budget_sql, params),
                            "linked_schedules": interpolate_sql(link_sql, params)})}


def get_project_history_from(con: duckdb.DuckDBPyConnection, pid: str | None = None,
                             fms_id: str | None = None,
                             managing_agency: str | None = None) -> dict:
    """Period-by-period snapshots for ONE project: schedule lens (pid) or budget
    lens (fms_id). Exactly one anchor id."""
    if (pid is None) == (fms_id is None):
        return {"error": "Provide exactly one of pid or fms_id."}
    if pid is not None and managing_agency is not None:
        return {"error": "managing_agency applies only to an FMS budget-line history."}
    if pid is not None:
        return attach_schedule_coverage(con, _schedule_history_answer(con, pid), pid=pid, history=True)
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
    current_sql = ("SELECT s.reporting_period, s.current_phase, s.lifecycle_status, "
                   "c.cumulative_variance_days, s.forecast_past_due, s.agency_project_name "
                   "FROM latest_project_state s LEFT JOIN cumulative_schedule_variance c "
                   "USING (pid) WHERE s.pid = ?")
    current_state = rows_as_dicts(con, current_sql, [pid])[0]
    current_state["cumulative_variance_days"] = signed_metric(current_state["cumulative_variance_days"])
    link_sql = _current_links_sql(where="pid = ?", select="fms_id, managing_agency",
                                  anchor_type="schedule")
    linked = rows_as_dicts(con, link_sql, [pid])
    env = mm_envelope(anchor_type="schedule", anchor_id=pid, linked=linked)
    return {"current_state": current_state, "periods": rows, **env,
            "provenance": provenance_block(
                definition="schedule_history rows for PID, period by period",
                scope={"pid": pid}, row_count=len(rows),
                reproduce_sql=interpolate_sql(sql, [pid]),
                components={"periods": interpolate_sql(sql, [pid]),
                            "current_state": interpolate_sql(current_sql, [pid]),
                            "linked_budgets": interpolate_sql(link_sql, [pid])})}


def _budget_history_answer(con: duckdb.DuckDBPyConnection, fms_id: str,
                           managing_agency: str | None) -> dict:
    where, params = _fms_line_filter(fms_id, managing_agency)
    sql = (f"SELECT fms_id, managing_agency, reporting_period, total_budget, "
           f"spend_to_date, spend_pct, budget_variance, budget_variance_pct "
           f"FROM budget_history WHERE {where} ORDER BY managing_agency, reporting_period")
    snap = rows_as_dicts(con, sql, params)
    original_sql = (f"SELECT fms_id, managing_agency, recorded_period, original_budget "
                    f"FROM original_budget WHERE {where}")
    orig = rows_as_dicts(con, original_sql, params)
    if not snap and not orig:
        return {"error": f"No budget (FMS line) found for {fms_id}"}
    orig_by_line = {(o["fms_id"], o["managing_agency"]): o for o in orig}
    # line-keyed FMS-system name (latest non-null; budget-only lines with no fb86 row
    # have no fms_location entry → None). One query covers every line for this id.
    names_sql = (f"SELECT fms_id, managing_agency, fms_project_name "
                 f"FROM fms_location WHERE {where}")
    names = {(r["fms_id"], r["managing_agency"]): r["fms_project_name"]
             for r in rows_as_dicts(con, names_sql, params)}
    snap_by_line: dict[tuple, list] = {}
    for r in snap:
        snap_by_line.setdefault((r["fms_id"], r["managing_agency"]), []).append(r)
    # first-seen order: snapshot lines first, then adoption-only lines
    keys = list(dict.fromkeys([*snap_by_line, *orig_by_line]))
    lines = []
    for k in keys:
        periods = []
        for r in snap_by_line.get(k, []):
            p = dict(r)
            p.pop("fms_id"); p.pop("managing_agency")
            p["budget_variance"] = signed_metric(p["budget_variance"], "budget")
            periods.append(p)
        o = orig_by_line.get(k)
        line = {"fms_id": k[0], "managing_agency": k[1],
                "fms_project_name": names.get(k),
                "original_budget": ({"amount": o["original_budget"],
                                     "recorded_period": o["recorded_period"],
                                     "note": "Adoption record (any calendar month), "
                                             "not a reporting snapshot."} if o else None),
                "periods": periods}
        if not periods:
            line["note"] = ("Adoption-only line: an original budget exists but no "
                            "reporting snapshot yet — normal for early allocations.")
        lines.append(line)
    link_sql = _current_links_sql(where=where, select="pid, managing_agency", anchor_type="budget")
    linked = rows_as_dicts(con, link_sql, params)
    env = mm_envelope(anchor_type="budget", anchor_id=fms_id, linked=linked)
    result = {"lines": lines, **env,
              "provenance": provenance_block(
                  definition="budget_history rows per line, period by period "
                             "(+ original_budget header)",
                  scope={"fms_id": fms_id, "managing_agency": managing_agency},
                  row_count=len(snap), reproduce_sql=interpolate_sql(sql, params),
                  components={"periods": interpolate_sql(sql, params),
                              "original_budget": interpolate_sql(original_sql, params),
                              "line_names": interpolate_sql(names_sql, params),
                              "linked_schedules": interpolate_sql(link_sql, params)})}
    if len(lines) > 1:
        result["grain_note"] = ("This FMS id is held by multiple managing agencies — "
                                "each is a distinct budget line ((managing_agency, "
                                "fms_id) grain); never sum across lines.")
    return result
