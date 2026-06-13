# tests/test_ingest.py
import duckdb

from od_cpd import ingest, schema


def _write_csv(path, header, rows):
    path.write_text(header + "\n" + "\n".join(rows) + "\n")


def test_load_raw_csv_into_table(tmp_path):
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    csv = tmp_path / "sched.csv"
    _write_csv(
        csv,
        "reporting_period,managing_agency,pid,agency_project_name,current_phase,"
        "completion_date,completion_date_type,variance_day,"
        "reason_for_forecast_completion_change,data_date",
        ["202601,DDC,101,Park,Construction,,Forecast,45,Late,2026-01-01"],
    )
    n = ingest.load_raw_csv(con, "raw_schedule_history", csv)
    assert n == 1
    got = con.execute(
        "SELECT managing_agency, variance_day FROM raw_schedule_history"
    ).fetchone()
    assert got == ("DDC", "45")   # stored as VARCHAR


def test_atomic_swap_replaces_file(tmp_path):
    final = tmp_path / "cpd.duckdb"
    final.write_text("OLD")
    shadow = tmp_path / "cpd_shadow.duckdb"
    shadow.write_text("NEW")
    ingest.atomic_swap(shadow, final)
    assert final.read_text() == "NEW"
    assert not shadow.exists()
    assert (tmp_path / "cpd.duckdb.bak").read_text() == "OLD"
