# tests/test_lookup.py
import duckdb
import pytest

from od_cpd import schema
from od_cpd.tools import lookup


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    schema.apply_schema(c)
    c.execute(
        "INSERT INTO meta VALUES "
        "('fb86-vt7u','reporting_period',1738000000,now(),100,'h',1,'202601','2025-09-30','2026-04-16')"
    )
    c.execute(
        "INSERT INTO agency_dim VALUES "
        "('ddc','Dept of Design & Construction',['DDC'],'DDC',true,true,8995,'managing'),"
        "('council','City Council',['City Council'],NULL,false,false,0,'sponsor')"
    )
    return c


def test_dataset_info_lists_per_dataset_freshness(con):
    info = lookup.dataset_info_from(con)
    fb = next(d for d in info["datasets"] if d["dataset_id"] == "fb86-vt7u")
    assert fb["latest_reporting_period"] == "202601"
    assert fb["fms_data_date"] == "2025-09-30"
    assert info["schedule_executors_count"] == 13


def test_list_agencies_filters_and_flags(con):
    res = lookup.list_agencies_from(con, contains="design")
    assert len(res["agencies"]) == 1
    a = res["agencies"][0]
    assert a["cpdw_acronym"] == "DDC"
    assert a["cpd_active"] is True
    assert res["provenance"]["reproduce_sql"] is None


def test_list_agencies_surfaces_role_default(con):
    res = lookup.list_agencies_from(con, contains="design")
    assert res["agencies"][0]["role_default"] == "managing"


def test_dataset_info_carries_domain_rules(con):
    # Portability: clients that drop MCP server instructions still get the primer
    # through the first tool call (gap 2).
    info = lookup.dataset_info_from(con)
    rules = info["domain_rules"]
    assert isinstance(rules, list) and len(rules) >= 5
    joined = " ".join(rules)
    assert "MANY-TO-MANY" in joined
    assert "fms_sponsor" in joined
    assert "BUDGET LINE" in joined          # location keying (gap 0)
    assert "original_budget" in joined      # variance bases (gap 0)
    assert "slippage" in joined             # terminology clause


def test_domain_rules_state_period_basis(con):
    rules = lookup.dataset_info_from(con)["domain_rules"]
    joined = " ".join(rules)
    assert "Reporting-period basis" in joined
    assert "all-history" in joined.lower()


import duckdb as _duckdb
from od_cpd import materialize as _materialize
from tests.test_materialize_normalized import _raw as _raw_fixture


def _built_con():
    c = _duckdb.connect(":memory:"); _raw_fixture(c); _materialize.materialize_all(c)
    return c


def test_describe_table_catalog_mode():
    out = lookup.describe_table_from(_built_con())
    names = {t["table"] for t in out["tables"]}
    assert {"schedule_history", "lifetime_budget_variance", "raw_project_detail"} <= names
    sh = next(t for t in out["tables"] if t["table"] == "schedule_history")
    assert sh["grain"] == "one row per (pid, reporting_period)"
    assert sh["kind"] == "analytics"


def test_describe_table_detail_mode_case_insensitive():
    out = lookup.describe_table_from(_built_con(), table="Schedule_History")
    assert out["table"] == "schedule_history"
    cols = {c["name"]: c for c in out["columns"]}
    assert cols["variance_day"]["type"] == "BIGINT"
    assert "signed" in cols["variance_day"]["note"]
    assert "pid" in cols


def test_describe_table_raw_points_to_describe_field():
    out = lookup.describe_table_from(_built_con(), table="raw_budget_history")
    assert "describe_field" in out["field_semantics"]
    assert all(c["type"] == "VARCHAR" for c in out["columns"])


def test_describe_table_unknown_errors_with_valid_names():
    out = lookup.describe_table_from(_built_con(), table="nope")
    assert "error" in out and "schedule_history" in out["error"]


def test_dataset_info_without_typed_tables_still_works(con):
    info = lookup.dataset_info_from(con)          # no materialize: no periods key
    fb = next(d for d in info["datasets"] if d["dataset_id"] == "fb86-vt7u")
    assert "available_periods" not in fb


def test_dataset_info_available_periods_from_typed_tables():
    c = _duckdb.connect(":memory:"); _raw_fixture(c)
    # a real ADOPTION record (NULL spend, off-cadence month) — must be excluded from the
    # snapshot period list (it lands in original_budget, not budget_history)
    c.execute(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) "
        "VALUES ('DDC','A','201903','80',NULL,NULL)")
    # a gyhf fiscal-year row so gyhf's periods come from project_budget_fy
    c.execute(
        "INSERT INTO raw_budget_fy (reporting_period, managing_agency, fms_id, fiscal_year,"
        " total_budget_city_non_city, city, non_city, spend) "
        "VALUES ('202601','DDC','A','2026','100','60','40','10')")
    _materialize.materialize_all(c)
    c.execute(
        "INSERT INTO meta VALUES "
        "('fb86-vt7u','reporting_period',1738000000,now(),100,'h',1,'202601','2025-09-30','2026-04-16'),"
        "('qj5n-h5qp','year_month_reported',1738000000,now(),100,'h',1,'202601',NULL,NULL),"
        "('gyhf-rsr3','reporting_period',1738000000,now(),100,'h',1,'202601',NULL,NULL)"
    )
    info = lookup.dataset_info_from(c)
    fb = next(d for d in info["datasets"] if d["dataset_id"] == "fb86-vt7u")
    qj = next(d for d in info["datasets"] if d["dataset_id"] == "qj5n-h5qp")
    gy = next(d for d in info["datasets"] if d["dataset_id"] == "gyhf-rsr3")
    assert fb["available_periods"] == ["202601"]
    # snapshots only: the 201903 adoption record is excluded by construction
    assert qj["available_periods"] == ["202509", "202601"]
    assert "adoption" in qj["period_note"].lower()           # original-budget caveat
    assert gy["available_periods"] == ["202601"]             # from project_budget_fy
