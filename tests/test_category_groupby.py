# tests/test_category_groupby.py — gap 5: category as a breakdown dimension.
import duckdb

from od_cpd import materialize
from od_cpd.tools.budget import budget_breakdown_from
from od_cpd.tools.schedule import schedule_breakdown_from
from tests.test_materialize_normalized import _raw


def _category_db():
    """Two-category DB wired for BOTH tools (schedule PIDs + budget snapshot rows)."""
    con = duckdb.connect(":memory:"); _raw(con)
    # PID 501 is funded by a Library line (LB1) AND a Bridges line (HB1);
    # PID 502 by a Library line only. Prefixes classify via categories.yaml tier 1.
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) VALUES (?,?,?,?,?,?,?,?)",
        [["202601", "DPR", "DPR", "501", "LB1", "100", "Construction", "K"],
         ["202601", "DPR", "DPR", "501", "HB1", "50", "Construction", "K"],
         ["202601", "DPR", "DPR", "502", "LB2", "200", "Construction", "Q"]])
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [["DPR", "LB1", "202601", "100", "10", "0"],
         ["DPR", "HB1", "202601", "50", "5", "0"],
         ["DPR", "LB2", "202601", "200", "20", "0"]])
    materialize.materialize_all(con)
    return con


def test_schedule_breakdown_by_category_counts_in_each():
    con = _category_db()
    r = schedule_breakdown_from(con, group_by="category")
    by = {g["category"]: g["n"] for g in r["groups"]}
    assert by["Library"] == 2          # PIDs 501 + 502
    assert by["Bridges"] == 1          # PID 501 counts AGAIN here (count-in-each)
    assert "EACH" in r["label"]        # non-additivity caveat is in-band


def test_budget_breakdown_by_category_is_line_grain_additive():
    con = _category_db()
    r = budget_breakdown_from(con, group_by="category")
    by = {g["category"]: g["value"] for g in r["groups"]}
    assert by["Library"] == 300.0      # LB1 100 + LB2 200
    assert by["Bridges"] == 50.0
