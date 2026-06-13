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
