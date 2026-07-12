# tests/test_schedule_tools.py
import duckdb

from od_cpd import schema, materialize
from od_cpd.tools.schedule import (
    schedule_breakdown_from,
    delay_reason_stats_from,
    schedule_changes_from,
)
from tests.test_materialize_normalized import _raw


def test_schedule_breakdown_count_by_agency():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency", metric="count",
                                statistic="count", period="current")
    ddc = next(g for g in r["groups"] if g["managing_agency"] == "DDC")
    assert ddc["value"] >= 1
    assert r["provenance"]["reproduce_sql"].lower().startswith("select")


def test_schedule_breakdown_mean_variance_signed():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency", metric="schedule_variance",
                                statistic="mean", period="current")
    assert r["period"] == "202601"
    assert "direction" in str(r["groups"][0])  # signed framing present


def test_schedule_breakdown_variance_excludes_placeholder_artifacts():
    con = duckdb.connect(":memory:"); _raw(con)
    # PID 777: a forecast-placeholder artifact (raw −364,938 days, like FDNY 3461)
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) "
        "VALUES ('202601','DDC','DDC','777','G','10','Design','K')")
    con.execute(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) "
        "VALUES ('202601','DDC','777','Design','2028-01-01','Forecast','-364938')")
    materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency",
                                metric="schedule_variance", statistic="mean")
    ddc = next(g for g in r["groups"] if g["managing_agency"] == "DDC")
    assert ddc["value"] == 45.0            # ungated, the artifact drags this to ~−182k
    assert r["excluded_artifacts"] == 1
    assert "excluded" in r["label"]


def test_schedule_breakdown_variance_no_artifacts_echoes_zero():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency",
                                metric="schedule_variance", statistic="mean")
    assert r["excluded_artifacts"] == 0
    assert "excluded" not in r["label"]    # no noise when nothing was dropped


def test_schedule_breakdown_offcadence_period_errors():
    # '202511' is off-cadence (Nov ∉ Jan/May/Sep) — must error, not silently match nothing.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency", period="202511")
    assert "error" in r


def test_schedule_breakdown_absent_cadence_period_errors():
    # '202105' is a valid cadence period but absent from the data — must error.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency", period="202105")
    assert "error" in r


def test_schedule_breakdown_real_explicit_period_still_works():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = schedule_breakdown_from(con, group_by="managing_agency", period="202601")
    assert "error" not in r
    assert r["period"] == "202601"


def test_delay_reason_stats_offcadence_period_errors():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = delay_reason_stats_from(con, period="202511")
    assert "error" in r


def test_delay_reason_stats_absent_cadence_period_errors():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = delay_reason_stats_from(con, period="202105")
    assert "error" in r


def test_delay_reason_stats_real_explicit_period_still_works():
    # 202601 exists (no delay reasons in the fixture → empty list, but NOT an error).
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = delay_reason_stats_from(con, period="202601")
    assert "error" not in r
    assert r["scope"] == "202601"


def test_delay_reason_stats_all_history_ignores_period():
    # scope='all_history' skips periods entirely — an off-cadence period must not error.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = delay_reason_stats_from(con, period="202511", scope="all_history")
    assert "error" not in r
    assert r["scope"] == "all_history"


def test_schedule_changes_delayed_returns_pid_101():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    # 101 has no row at 202509 (absent then = not delayed) and +45 days at 202601
    r = schedule_changes_from(con, change_type="delayed",
                              from_period="202509", to_period="202601")
    pids = {c["pid"] for c in r["changes"]}
    assert "101" in pids
    assert r["change_type"] == "delayed"
    assert r["provenance"]["reproduce_sql"].lower().startswith("select")


import duckdb as _duckdb
from tests.test_agency_scope import _scope_db


def test_schedule_breakdown_agency_sponsor_default():
    con = _scope_db(_duckdb.connect(":memory:"))
    r = schedule_breakdown_from(con, group_by="borough", agency="DOC")
    assert r["agency_scope"]["role"] == "sponsor"
    # DOC owns PIDs 201 (borough M) and 202 (borough Q); count is over those only
    total = sum(g["n"] for g in r["groups"])
    assert total == 2


def test_schedule_breakdown_agency_managing_override():
    con = _scope_db(_duckdb.connect(":memory:"))
    r = schedule_breakdown_from(con, group_by="borough", agency="DOC", agency_role="managing")
    total = sum(g["n"] for g in r["groups"])
    assert total == 1     # only the DOC-managed PID 201


def test_schedule_breakdown_by_sponsor_splits_composite():
    # PID 203 in _scope_db is sponsored by the composite string 'DOT, DPR'; grouping by
    # sponsor_agency must yield atomic 'DOT'/'DPR' buckets, never a 'DOT, DPR' key.
    con = _scope_db(_duckdb.connect(":memory:"))
    r = schedule_breakdown_from(con, group_by="sponsor_agency")
    keys = {g["sponsor_agency"] for g in r["groups"]}
    assert "," not in "".join(keys)          # no composite bucket label
    assert {"DOT", "DPR"} <= keys            # 203's owners appear as separate buckets


def test_delay_reason_stats_coverage_counts():
    con = duckdb.connect(":memory:"); _raw(con)
    # a delayed PID WITHOUT a reason (variance>0, reason NULL)
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) "
        "VALUES ('202601','DDC','DDC','555','H','10','Design','K')")
    con.execute(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) "
        "VALUES ('202601','DDC','555','Design','2027-06-01','Forecast','30')")
    materialize.materialize_all(con)
    r = delay_reason_stats_from(con)
    cov = r["coverage"]
    assert cov["delayed_total"] == cov["with_reason"] + cov["without_reason"]
    # Both delayed PIDs at 202601 (101 var=45, 555 var=30) have NULL reasons in the fixture.
    assert cov["delayed_total"] == 2
    assert cov["with_reason"] == 0
    assert cov["without_reason"] == 2


def test_schedule_changes_rows_carry_agency_project_name():
    con = duckdb.connect(":memory:"); _raw(con)
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough, agency_project_name) "
        "VALUES ('202509','DDC','DDC','101','A','90','Design','K','Park A')")
    materialize.materialize_all(con)
    r = schedule_changes_from(con, "delayed", from_period="202509", to_period="202601")
    assert all("agency_project_name" in row for row in r["changes"])
