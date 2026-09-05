# tests/test_budget_tools.py
import duckdb

from od_cpd import schema, materialize
from od_cpd.tools.budget import budget_breakdown_from, budget_change_from
from tests.test_materialize_normalized import _raw
from tests.test_agency_scope import _scope_db


def test_budget_breakdown_by_agency_deduped():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = budget_breakdown_from(con, group_by="managing_agency", metric="total_budget",
                              period="current")
    ddc = next(g for g in r["groups"] if g["managing_agency"] == "DDC")
    assert ddc["value"] > 0
    assert "(fms_id, managing_agency)" in r["provenance"]["scope"]["dedup"]


def test_budget_breakdown_offcadence_period_errors():
    # '202511' is off-cadence (Nov ∉ Jan/May/Sep) — must error, not silently match nothing.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = budget_breakdown_from(con, period="202511")
    assert "error" in r


def test_budget_breakdown_absent_cadence_period_errors():
    # '202105' is a valid cadence period but absent from the data — must error.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = budget_breakdown_from(con, period="202105")
    assert "error" in r


def test_budget_breakdown_real_explicit_period_still_works():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = budget_breakdown_from(con, period="202601")
    assert "error" not in r
    assert r["period"] == "202601"


def test_budget_change_agency_ddc_increase():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    # Populate agency_dim so resolve_agency_scope can find DDC (role=managing)
    con.execute(
        "INSERT INTO agency_dim VALUES "
        "('ddc','Dept of Design & Construction',['DDC'],'DDC',true,true,0,'managing')"
    )
    # DDC@202509 deduped = A:90 ; DDC@202601 deduped = A:100 + B:50 + C:200 = 350
    r = budget_change_from(con, target="agency:DDC", from_period="202509",
                           to_period="202601", metric="total_budget")
    assert r["from_value"] == 90.0
    assert r["to_value"] == 350.0
    assert r["change"]["value"] == 260.0
    assert r["change"]["direction"] == "increased"
    assert r["provenance"]["scope"]["dedup"] == "(fms_id, managing_agency)"


def test_budget_breakdown_agency_sponsor_scope():
    con = _scope_db(duckdb.connect(":memory:"))
    r = budget_breakdown_from(con, agency="DOC")
    assert r["agency_scope"]["role"] == "sponsor"
    total = sum(g["value"] for g in r["groups"])
    assert total == 9500.0        # J1 500 + J2 9000 (DOC-owned, regardless of holder)


def test_budget_breakdown_agency_managing_override():
    con = _scope_db(duckdb.connect(":memory:"))
    r = budget_breakdown_from(con, agency="DOC", agency_role="managing")
    total = sum(g["value"] for g in r["groups"])
    assert total == 500.0         # only the DOC-held line J1


def test_budget_change_agency_role_aware_target():
    con = _scope_db(duckdb.connect(":memory:"))
    r = budget_change_from(con, target="agency:DOC", from_period="202601", to_period="202601")
    assert r["agency_scope"]["role"] == "sponsor"
    assert "as-of" in r["label"].lower()      # temporal caveat surfaced


def _shared_fms_db():
    """_raw + one FMS id held by TWO managing agencies (two distinct budget lines)."""
    con = duckdb.connect(":memory:"); _raw(con)
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date) VALUES (?,?,?,?,?)",
        [["DDC", "SHARED", "202509", "100", "0"],
         ["DDC", "SHARED", "202601", "80", "0"],    # DDC's line cut by 20
         ["DOT", "SHARED", "202509", "50", "0"],
         ["DOT", "SHARED", "202601", "90", "0"]])   # DOT's line grew by 40
    materialize.materialize_all(con)
    return con


def test_budget_change_fms_multi_agency_lists_lines_never_sums():
    # Grain rule: (managing_agency, fms_id). A cross-agency sum would report +20 and
    # mask DDC's cut with DOT's growth — the tool must list per-line deltas instead.
    r = budget_change_from(_shared_fms_db(), target="fms:SHARED",
                           from_period="202509", to_period="202601")
    assert r["change"]["value"] is None
    assert r["from_value"] is None and r["to_value"] is None
    lines = {l["managing_agency"]: l for l in r["lines"]}
    assert lines["DDC"]["change"]["value"] == -20.0
    assert lines["DDC"]["change"]["direction"] == "decreased"
    assert lines["DOT"]["change"]["value"] == 40.0
    assert lines["DOT"]["change"]["direction"] == "increased"
    assert "(managing_agency, fms_id)" in r["label"]


def test_budget_change_fms_managing_agency_scopes_to_one_line():
    r = budget_change_from(_shared_fms_db(), target="fms:SHARED",
                           from_period="202509", to_period="202601",
                           managing_agency="DDC")
    assert r["from_value"] == 100.0 and r["to_value"] == 80.0
    assert r["change"]["value"] == -20.0
    assert r["managing_agency"] == "DDC"
    assert "lines" not in r


def test_budget_change_fms_single_agency_keeps_flat_shape():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = budget_change_from(con, target="fms:A", from_period="202509", to_period="202601")
    assert r["from_value"] == 90.0 and r["to_value"] == 100.0
    assert r["change"]["value"] == 10.0
    assert r["managing_agency"] == "DDC"    # single line: the holder is echoed
    assert "lines" not in r


def test_budget_change_agency_target_rejects_managing_agency_param():
    con = _scope_db(duckdb.connect(":memory:"))
    r = budget_change_from(con, target="agency:DOC", from_period="202509",
                           to_period="202601", managing_agency="DDC")
    assert "error" in r


def test_budget_change_agency_missing_period_no_fabricated_delta():
    # Agency-path coverage for the missing-period guard (the fms: path is covered in
    # test_review_fixes): DOC budget exists only at 202601, so a 202501 from_period has no
    # data and must NOT fabricate a delta.
    con = _scope_db(duckdb.connect(":memory:"))
    r = budget_change_from(con, target="agency:DOC", from_period="202501", to_period="202601")
    assert r["from_value"] is None
    assert r["change"]["value"] is None
    assert r["change"]["direction"] is None


def test_budget_options_validate_without_an_agency_filter():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    assert "error" in budget_breakdown_from(con, agency_role="invalid")
    assert "error" in budget_change_from(con, "fms:A", "202509", "202601", agency_role="invalid")
    for period in ("bad-period", "202602"):
        assert "error" in budget_change_from(con, "fms:A", period, "202601")
