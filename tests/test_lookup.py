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
