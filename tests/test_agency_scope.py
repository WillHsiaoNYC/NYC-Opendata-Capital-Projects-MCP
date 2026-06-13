# tests/test_agency_scope.py
import duckdb
import pytest

from od_cpd import materialize, schema
from od_cpd.tools.agency_scope import resolve_agency, resolve_agency_scope
from tests.test_materialize_normalized import _raw


@pytest.fixture
def con():
    c = duckdb.connect(":memory:"); schema.apply_schema(c)
    c.execute(
        "INSERT INTO agency_dim VALUES "
        "('doc','Department of Correction',['DOC','Department of Correction'],'DOC',true,true,0,'sponsor'),"
        "('ddc','Dept of Design & Construction',['DDC'],'DDC',true,true,0,'managing'),"
        "('hh','NYC Health + Hospitals',['H+H','HHC','NYC Health + Hospitals'],'HHC',true,false,0,'sponsor'),"
        "('council','City Council',['City Council'],NULL,false,false,0,'sponsor')"
    )
    return c


def test_resolve_by_acronym_slug_and_alias(con):
    assert resolve_agency(con, "DOC")["acronym"] == "DOC"
    assert resolve_agency(con, "doc")["acronym"] == "DOC"
    assert resolve_agency(con, "Department of Correction")["acronym"] == "DOC"


def test_resolve_carries_role_default(con):
    assert resolve_agency(con, "ddc")["role_default"] == "managing"
    assert resolve_agency(con, "doc")["role_default"] == "sponsor"


def test_resolve_unifies_label_variants(con):
    info = resolve_agency(con, "H+H")
    assert info["acronym"] == "HHC"
    assert "H+H" in info["variants"] and "HHC" in info["variants"]


def test_resolve_ambiguous_acronym_prefers_capital_agency():
    # 'HRO' is the cpdw_acronym of NYCHA and an alias of the dictionary-only HRO entry;
    # the deterministic tiebreaker must pick the capital-project agency (non-null acronym).
    c = duckdb.connect(":memory:"); schema.apply_schema(c)
    c.execute(
        "INSERT INTO agency_dim VALUES "
        "('nycha','NYC Housing Authority',['NYCHA'],'HRO',false,false,0,'sponsor'),"
        "('hro','Housing Recovery Office',['HRO'],NULL,NULL,false,0,'sponsor')"
    )
    info = resolve_agency(c, "HRO")
    assert info is not None and info["slug"] == "nycha" and info["acronym"] == "HRO"


def test_scope_rejects_invalid_role(con):
    r = resolve_agency_scope(con, "DOC", agency_role="owner", entity="schedule")
    assert "error" in r and "agency_role" in r["error"]


def test_resolve_unknown_is_none(con):
    assert resolve_agency(con, "nope") is None


def test_resolve_dictionary_only_has_no_acronym(con):
    assert resolve_agency(con, "council")["acronym"] is None


def _scope_db(c):
    """Synthetic DB mirroring the real sponsor-vs-managing split.
    Reuses _raw (PID 101 DDC/DDC fms A,B; PID 102 DDC builds / DPR owns fms C),
    and adds: a DOC self-managed line (fms J1), a DDC-managed DOC-sponsored line
    (fms J2 — the BBJ analog), and a composite-sponsor PID (fms J3, 'DOT, DPR')."""
    _raw(c)
    c.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase) VALUES (?,?,?,?,?,?,?)",
        [["202601", "DOC", "DOC", "201", "J1", "500", "Construction"],
         ["202601", "DDC", "DOC", "202", "J2", "9000", "Construction"],
         ["202601", "DDC", "DOT, DPR", "203", "J3", "700", "Construction"]])
    c.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [["DOC", "J1", "202601", "500", "0", "0"],
         ["DDC", "J2", "202601", "9000", "0", "0"],
         ["DDC", "J3", "202601", "700", "0", "0"]])
    materialize.materialize_all(c)
    c.execute(
        "INSERT INTO agency_dim VALUES "
        "('doc','Department of Correction',['DOC','Department of Correction'],'DOC',true,true,0,'sponsor'),"
        "('ddc','Dept of Design & Construction',['DDC'],'DDC',true,true,0,'managing'),"
        "('dpr','Dept of Parks',['DPR'],'DPR',true,true,0,'sponsor'),"
        "('dot','Dept of Transportation',['DOT'],'DOT',true,true,0,'sponsor')")
    return c


@pytest.fixture
def sdb():
    return _scope_db(duckdb.connect(":memory:"))


def _pids(con, where):
    return {r[0] for r in con.execute(
        f"SELECT pid FROM latest_project_state WHERE {where}").fetchall()}


def _fms(con, where):
    return {r[0] for r in con.execute(
        f"SELECT fms_id FROM lifetime_budget_variance WHERE {where}").fetchall()}


def test_scope_schedule_sponsor_default(sdb):
    s = resolve_agency_scope(sdb, "DOC", entity="schedule")
    assert s["agency_scope"]["role"] == "sponsor"
    assert _pids(sdb, s["where"]) == {"201", "202"}   # self-managed + DDC-managed, both DOC-owned


def test_scope_schedule_manager_defaults_to_managing(sdb):
    s = resolve_agency_scope(sdb, "DDC", entity="schedule")
    assert s["agency_scope"]["role"] == "managing"
    assert "201" not in _pids(sdb, s["where"])        # 201 is DOC-managed
    assert {"101", "102", "202", "203"} <= _pids(sdb, s["where"])  # everything DDC builds


def test_scope_schedule_sponsor_matches_composite(sdb):
    s = resolve_agency_scope(sdb, "DPR", entity="schedule")
    assert "203" in _pids(sdb, s["where"])            # 'DOT, DPR' split -> DPR matches
    assert "102" in _pids(sdb, s["where"])            # DPR-owned, DDC-built


def test_scope_budget_sponsor_crosses_to_builder_held_lines(sdb):
    s = resolve_agency_scope(sdb, "DOC", entity="budget")
    assert _fms(sdb, s["where"]) == {"J1", "J2"}       # J2 is DDC-held but DOC-owned
    assert "do not sum" in s["agency_scope"]["note"]


def test_scope_budget_managing_override(sdb):
    s = resolve_agency_scope(sdb, "DOC", agency_role="managing", entity="budget")
    assert _fms(sdb, s["where"]) == {"J1"}             # only the DOC-held line


def test_scope_alias_qualifies_columns(con):  # only needs agency_dim, not materialized data
    s = resolve_agency_scope(con, "DDC", entity="schedule", alias="t")
    assert "t.managing_agency" in s["where"]


def test_scope_unknown_agency_errors(con):  # only needs agency_dim, not materialized data
    assert "error" in resolve_agency_scope(con, "Ministry of Magic", entity="schedule")
