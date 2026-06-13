# tests/test_schema.py
import duckdb

from od_cpd import schema


def test_apply_schema_creates_all_tables():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables"
    ).fetchall()}
    assert {
        "raw_project_detail", "raw_budget_fy",
        "raw_budget_history", "raw_schedule_history",
        "meta", "agency_dim",
    } <= tables


def test_raw_project_detail_has_27_varchar_columns():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    cols = con.execute("PRAGMA table_info('raw_project_detail')").fetchall()
    assert len(cols) == 27
    assert all(c[2] == "VARCHAR" for c in cols)  # c[2] = column type


def test_raw_table_columns_match_spec():
    assert schema.RAW_COLUMNS["raw_budget_history"][2] == "year_month_reported"
    assert "actual_construction_end" in schema.RAW_COLUMNS["raw_project_detail"]
