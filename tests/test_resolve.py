# tests/test_resolve.py
import duckdb
from od_cpd import schema, materialize
from od_cpd.tools.resolve import resolve_from
from tests.test_materialize_normalized import _raw


def test_resolve_by_name_buckets_by_entity():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = resolve_from(con, "Park")
    pids = [m["pid"] for m in r["schedule_matches"]]
    assert "101" in pids
    assert r["provenance"]["reproduce_sql"] is not None


def test_resolve_by_exact_pid():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = resolve_from(con, "101")
    assert any(m["pid"] == "101" for m in r["schedule_matches"])


def test_resolve_exposes_sponsor_agency():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = resolve_from(con, "Lib B")   # PID 102: DDC-managed, DPR-sponsored
    m = next(m for m in r["schedule_matches"] if m["pid"] == "102")
    assert m["sponsor_agency"] == "DPR"
