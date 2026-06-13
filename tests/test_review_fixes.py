# tests/test_review_fixes.py
"""Regression tests for the /code-review correctness fixes."""
import duckdb
import pytest

from od_cpd import schema, materialize
from od_cpd.tools._common import interpolate_sql, mm_envelope
from od_cpd.tools.budget import budget_breakdown_from, budget_change_from
from od_cpd.tools.schedule import schedule_changes_from
from od_cpd.tools.ranking import rank_projects_from


# ---- _common helpers -------------------------------------------------------

def test_interpolate_sql_escapes_quotes_and_handles_question_marks_and_none():
    # value containing a single quote → doubled; a '?' in a value is not re-substituted
    sql = "WHERE a = ? AND b = ? AND c = ?"
    out = interpolate_sql(sql, ["O'Brien", "why?", None])
    assert out == "WHERE a = 'O''Brien' AND b = 'why?' AND c = NULL"


def test_interpolate_sql_mismatch_returns_unchanged():
    assert interpolate_sql("WHERE a = ?", []) == "WHERE a = ?"


def test_mm_envelope_zero_linked_is_not_many_to_many():
    env = mm_envelope(anchor_type="schedule", anchor_id="1", linked=[])
    assert "many-to-many" not in env["caveat"]
    assert "No linked" in env["caveat"]
    assert env["linked_budgets"] == []


# ---- SQL-level fixes (build a tiny DB) -------------------------------------

def _base(con):
    schema.apply_schema(con)


def test_budget_history_dedups_duplicate_rows_with_different_total_budget():
    con = duckdb.connect(":memory:"); _base(con)
    # Same (fms_id, managing_agency, period) twice with DIFFERENT total_budget (the qj5n quirk)
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [["DDC", "X", "202601", "100", "10", "0"],
         ["DDC", "X", "202601", "150", "0", "5"]],  # duplicate key, different total
    )
    materialize.build_normalized(con)
    rows = con.execute("SELECT count(*), max(total_budget) FROM budget_history "
                       "WHERE fms_id='X' AND managing_agency='DDC' AND reporting_period='202601'"
                       ).fetchone()
    assert rows[0] == 1          # exactly ONE row (deduped), not two
    assert rows[1] == 150.0      # max() canonicalization
    # and budget_breakdown does not double-count
    materialize.build_analytics(con)
    r = budget_breakdown_from(con, group_by="managing_agency", metric="total_budget", period="202601")
    ddc = next(g for g in r["groups"] if g["managing_agency"] == "DDC")
    assert ddc["value"] == 150.0  # not 250 (the double-counted sum)


def test_deterministic_lifecycle_completes_on_any_completion_phase():
    con = duckdb.connect(":memory:"); _base(con)
    # PID 9 has two fb86 rows same period: one Close-out (→completed), one Construction.
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id,"
        " total_budget, current_phase) VALUES (?,?,?,?,?,?)",
        [["202601", "DDC", "9", "A", "10", "Close-out"],
         ["202601", "DDC", "9", "B", "20", "Construction"]],
    )
    materialize.materialize_all(con)
    # No actual_construction_end, but a Close-out link → deterministically 'completed'
    status = con.execute("SELECT lifecycle_status FROM latest_project_state WHERE pid='9'").fetchone()[0]
    assert status == "completed"


def test_schedule_changes_completed_catches_first_seen_completed_pid():
    con = duckdb.connect(":memory:"); _base(con)
    # PID 5 appears ONLY at to_period (202601), already completed (absent at from_period 202509)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id,"
        " total_budget, current_phase, actual_construction_end) VALUES (?,?,?,?,?,?,?)",
        [["202601", "DDC", "5", "A", "10", "(Completed)", "2025-12-01"]],
    )
    materialize.materialize_all(con)
    r = schedule_changes_from(con, change_type="completed",
                              from_period="202509", to_period="202601")
    assert any(c["pid"] == "5" for c in r["changes"])   # LEFT JOIN catches it


def test_budget_change_missing_period_no_fabricated_delta():
    con = duckdb.connect(":memory:"); _base(con)
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date) VALUES (?,?,?,?,?)",
        [["DEP", "Z", "202601", "100", "0"]],  # only 202601 exists; snapshot-shaped row
    )
    materialize.materialize_all(con)
    # This regression is about delta-fabrication, not agency resolution — use an fms: target
    # so it exercises the same missing-period branch without depending on agency_dim/roles.
    r = budget_change_from(con, target="fms:Z", from_period="202501", to_period="202601")
    assert r["from_value"] is None and r["to_value"] == 100.0
    assert r["change"]["value"] is None      # not fabricated 100
    assert r["change"]["direction"] is None


def test_budget_change_rejects_bad_target():
    con = duckdb.connect(":memory:"); _base(con); materialize.materialize_all(con)
    assert "error" in budget_change_from(con, target="DEP", from_period="a", to_period="b")


def test_rank_projects_negative_n_does_not_crash():
    con = duckdb.connect(":memory:"); _base(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id,"
        " total_budget, current_phase) VALUES (?,?,?,?,?,?)",
        [["202601", "DDC", "1", "A", "10", "Construction"]],
    )
    con.executemany(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, variance_day) VALUES (?,?,?,?,?)",
        [["202601", "DDC", "1", "Construction", "5"]],
    )
    materialize.materialize_all(con)
    r = rank_projects_from(con, entity="schedule", rank_by="period_variance_days", n=-1)
    assert "error" not in r                 # clamped, no crash
    assert len(r["rows"]) <= 1
