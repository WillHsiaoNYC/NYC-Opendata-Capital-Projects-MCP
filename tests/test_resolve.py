# tests/test_resolve.py
import duckdb
import pytest
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


def test_resolution_paginates_stably_with_exact_ids_before_partial_ids_and_names():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    rows = [["202601", "DDC", str(i), f"F{i}", f"123 bridge {i}", f"123 bridge {i}"]
            for i in range(500, 565)]
    rows += [["202601", "DDC", "123", "123", "Unrelated exact name", "Unrelated exact name"],
             ["202601", "DDC", "91230", "PART123", "Unrelated partial name", "Unrelated partial name"]]
    con.executemany("INSERT INTO raw_project_detail (reporting_period, managing_agency, "
                    "pid, fms_id, agency_project_name, fms_project_name) VALUES (?,?,?,?,?,?)", rows)
    materialize.materialize_all(con)
    first = resolve_from(con, "123")
    second = resolve_from(con, "123", offset=50)
    for bucket, key, expected in (("schedule", "pid", ["123", "91230"]),
                                  ("budget", "fms_id", ["123", "PART123"])):
        assert [r[key] for r in first[f"{bucket}_matches"][:2]] == expected
        assert first["pagination"][bucket]["total_count"] == 67
        assert first["pagination"][bucket]["truncated"] is True
        assert first["pagination"][bucket]["next_offset"] == 50
        assert second["pagination"][bucket]["next_offset"] is None
        combined = first[f"{bucket}_matches"] + second[f"{bucket}_matches"]
        assert len({r[key] for r in combined}) == 67
        assert combined == resolve_from(con, "123", limit=500)[f"{bucket}_matches"]
        assert con.execute(first["provenance"][bucket]["components"]["total_count"]).fetchone() == (67,)


def test_resolution_renamed_line_matches_history_but_displays_one_latest_name_per_holder():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    rows = [
        ["202509", "DDC", "F", "Old bridge"],
        ["202601", "DDC", "F", "Alpha terminal"],
        ["202601", "DDC", "F", "Beta terminal"],
        ["202605", "DDC", "F", None],
        ["202601", "DPR", "F", "Park bridge"],
    ]
    con.executemany("INSERT INTO raw_project_detail (reporting_period, managing_agency, "
                    "fms_id, fms_project_name) VALUES (?,?,?,?)", rows)
    materialize.materialize_all(con)
    result = resolve_from(con, "bridge")
    assert [(r["managing_agency"], r["fms_project_name"]) for r in result["budget_matches"]] == [
        ("DDC", "Alpha terminal"), ("DPR", "Park bridge"),
    ]
    assert result["pagination"]["budget"]["total_count"] == 2


@pytest.mark.parametrize("query, expected", [("%", "A%X"), ("_", "A_X"), ("\\", "A\\X")])
def test_resolution_partial_identifiers_escape_wildcards(query, expected):
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    con.executemany("INSERT INTO raw_project_detail (reporting_period, managing_agency, "
                    "pid, fms_id) VALUES ('202601', 'DDC', ?, ?)",
                    [[value, value] for value in ("A%X", "A_X", "A\\X", "ABX")])
    materialize.materialize_all(con)
    result = resolve_from(con, query)
    assert [r["pid"] for r in result["schedule_matches"]] == [expected]
    assert [r["fms_id"] for r in result["budget_matches"]] == [expected]


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"offset": 1.5}])
def test_resolver_validates_pagination_before_queries(kwargs):
    assert "error" in resolve_from(None, "bridge", **kwargs)
