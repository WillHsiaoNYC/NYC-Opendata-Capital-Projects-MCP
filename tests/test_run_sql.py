# tests/test_run_sql.py
import duckdb
import pytest

from od_cpd.tools.sql import validate_select, run_sql_on


def test_validate_rejects_non_select():
    for bad in ["INSERT INTO t VALUES (1)", "DROP TABLE t", "UPDATE t SET a=1",
                "ATTACH 'x'", "PRAGMA database_list", "COPY t TO 'f'"]:
        with pytest.raises(ValueError):
            validate_select(bad)


def test_validate_rejects_multi_statement():
    with pytest.raises(ValueError):
        validate_select("SELECT 1; SELECT 2")


def test_validate_allows_select_and_with():
    validate_select("SELECT 1")
    validate_select("WITH x AS (SELECT 1) SELECT * FROM x")


def test_run_sql_inline_caps_rows():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT * FROM range(10) AS r(n)")
    result = run_sql_on(con, "SELECT n FROM t", row_cap=3)
    assert result["truncated"] is True
    assert len(result["rows"]) == 3
    assert result["provenance"]["reproduce_sql"].startswith("SELECT")


def test_run_sql_not_truncated_when_small():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT * FROM range(2) AS r(n)")
    result = run_sql_on(con, "SELECT n FROM t", row_cap=10)
    assert result["truncated"] is False
    assert len(result["rows"]) == 2


def _db_with_meta(latest="202601"):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE meta (latest_reporting_period VARCHAR)")
    con.execute("INSERT INTO meta VALUES (?)", [latest])
    return con


def test_run_sql_echoes_latest_period():
    con = _db_with_meta("202601")
    con.execute("CREATE TABLE t AS SELECT 1 AS n")
    r = run_sql_on(con, "SELECT n FROM t")
    assert r["latest_reporting_period"] == "202601"


def test_run_sql_latest_period_none_without_meta():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS n")
    r = run_sql_on(con, "SELECT n FROM t")
    assert r["latest_reporting_period"] is None


def test_run_sql_flags_all_history_table():
    con = _db_with_meta("202601")
    con.execute("CREATE TABLE fms_location AS SELECT 'A' AS fms_id, 'K' AS borough")
    r = run_sql_on(con, "SELECT borough, count(*) FROM fms_location GROUP BY borough")
    assert "period_basis_note" in r
    assert "fms_location" in r["period_basis_note"]
    assert "202601" in r["period_basis_note"]


def test_run_sql_no_note_for_period_scoped_query():
    con = _db_with_meta("202601")
    con.execute("CREATE TABLE raw_project_detail AS "
                "SELECT '202601' AS reporting_period, 'A' AS fms_id")
    r = run_sql_on(con, "SELECT count(*) FROM raw_project_detail "
                        "WHERE reporting_period='202601'")
    assert "period_basis_note" not in r


def test_run_sql_note_ignores_string_literal():
    con = _db_with_meta("202601")
    con.execute("CREATE TABLE t AS SELECT 'fms_location' AS s")
    r = run_sql_on(con, "SELECT s FROM t WHERE s = 'fms_location'")
    assert "period_basis_note" not in r


def test_run_sql_note_pluralizes_for_multiple_tables():
    con = _db_with_meta("202601")
    con.execute("CREATE TABLE fms_location AS SELECT 'A' AS fms_id")
    con.execute("CREATE TABLE fms_sponsor AS SELECT 'A' AS fms_id")
    r = run_sql_on(con, "SELECT * FROM fms_location JOIN fms_sponsor USING (fms_id)")
    assert "fms_location, fms_sponsor are all-history" in r["period_basis_note"]
