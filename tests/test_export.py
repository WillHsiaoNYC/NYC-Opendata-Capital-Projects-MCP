# tests/test_export.py
import duckdb

from od_cpd import export


def test_write_csv(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a, 'x' AS b")
    out = export.write_csv(con, "SELECT * FROM t", tmp_path / "o.csv")
    assert out.exists()
    assert "a,b" in out.read_text()
    assert "1,x" in out.read_text()


def test_write_xlsx_two_sheets(tmp_path):
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t AS SELECT 1 AS a")
    prov = {"definition": "d", "reproduce_sql": "SELECT * FROM t"}
    out = export.write_xlsx(con, "SELECT * FROM t", prov, tmp_path / "o.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(out)
    assert set(wb.sheetnames) == {"data", "methodology"}
