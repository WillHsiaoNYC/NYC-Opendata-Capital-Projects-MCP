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


def test_budget_change_agency_missing_period_no_fabricated_delta():
    # Agency-path coverage for the missing-period guard (the fms: path is covered in
    # test_review_fixes): DOC budget exists only at 202601, so a 202501 from_period has no
    # data and must NOT fabricate a delta.
    con = _scope_db(duckdb.connect(":memory:"))
    r = budget_change_from(con, target="agency:DOC", from_period="202501", to_period="202601")
    assert r["from_value"] is None
    assert r["change"]["value"] is None
    assert r["change"]["direction"] is None
