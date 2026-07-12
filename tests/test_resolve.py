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
    assert r["provenance"]["schedule"]["reproduce_sql"] is not None


def test_resolve_by_exact_pid():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = resolve_from(con, "101")
    assert any(m["pid"] == "101" for m in r["schedule_matches"])


def test_resolve_exposes_sponsor_agency():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = resolve_from(con, "Lib B")   # PID 102: DDC-managed, DPR-sponsored
    m = next(m for m in r["schedule_matches"] if m["pid"] == "102")
    assert m["sponsor_agency"] == "DPR"


def test_resolve_provenance_is_per_bucket_and_reproducible():
    # "D" is an FMS line (QPL, null PID) — it matches the BUDGET bucket only. This is
    # exactly the case the old single combined-count provenance mis-reported: a nonzero
    # row_count against a schedule-only reproduce_sql that reproduces zero rows.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = resolve_from(con, "D")
    assert r["schedule_matches"] == []
    assert [m["fms_id"] for m in r["budget_matches"]] == ["D"]

    prov = r["provenance"]
    # Both buckets expose their own self-contained, single-statement reproduce_sql.
    assert prov["schedule"]["reproduce_sql"] is not None
    assert prov["budget"]["reproduce_sql"] is not None

    # Per-bucket row_count matches that bucket's own match list...
    assert prov["schedule"]["row_count"] == len(r["schedule_matches"]) == 0
    assert prov["budget"]["row_count"] == len(r["budget_matches"]) == 1

    # ...and re-running each exposed reproduce_sql reproduces its reported row_count.
    for bucket in ("schedule", "budget"):
        got = len(con.execute(prov[bucket]["reproduce_sql"]).fetchall())
        assert got == prov[bucket]["row_count"]
