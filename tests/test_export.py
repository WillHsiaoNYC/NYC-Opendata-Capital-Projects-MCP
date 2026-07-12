# tests/test_export.py
import duckdb
import pytest

from od_cpd import export


def test_write_csv(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a, 'x' AS b")
    out = export.write_csv(con, "SELECT * FROM t", tmp_path / "o.csv")
    assert out.exists()
    assert "a,b" in out.read_text()
    assert "1,x" in out.read_text()


def test_write_csv_trailing_comment(tmp_path):
    # A query ending in a `--` line comment must not swallow COPY's closing paren + TO
    # clause. Fails on the unwrapped one-line COPY (ParserException); the newline-before-')'
    # wrap fixes it — mirrors the inline path's guard.
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a, 'x' AS b")
    out = export.write_csv(con, "SELECT * FROM t -- trailing comment", tmp_path / "o.csv")
    assert out.exists()
    assert "a,b" in out.read_text()
    assert "1,x" in out.read_text()


def test_write_xlsx_trailing_comment(tmp_path):
    # write_xlsx executes the query directly (no paren wrap), so a trailing comment is
    # already harmless — this guards that it stays that way.
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    prov = {"definition": "d", "reproduce_sql": "SELECT * FROM t"}
    out = export.write_xlsx(con, "SELECT * FROM t -- trailing comment", prov, tmp_path / "o.xlsx")
    assert out.exists()


def test_write_csv_timeout(tmp_path):
    # A pathologically slow, uncapped query must be interrupted by the timeout rather than
    # hang the server. range() cross join = ~10^18 rows; a 1s timeout fires long first.
    con = duckdb.connect(":memory:")
    slow = "SELECT count(*) FROM range(1000000000) a, range(1000000000) b"
    with pytest.raises(duckdb.Error):
        export.write_csv(con, slow, tmp_path / "o.csv", timeout=1)


def test_write_xlsx_two_sheets(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    prov = {"definition": "d", "reproduce_sql": "SELECT * FROM t"}
    out = export.write_xlsx(con, "SELECT * FROM t", prov, tmp_path / "o.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(out)
    assert set(wb.sheetnames) == {"data", "methodology"}
