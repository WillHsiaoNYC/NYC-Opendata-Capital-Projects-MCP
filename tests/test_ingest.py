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


def test_build_agency_dim_cpd_active_never_null():
    # Regression: for dictionary-only agencies (cpdw_acronym IS NULL), the buggy
    # `NULL IN (<non-empty set>)` evaluated to NULL rather than FALSE, leaving
    # cpd_active NULL. An acronym-less agency is definitively absent from CPD data,
    # so it must be FALSE, and no agency_dim row may carry a NULL flag.
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    # raw_project_detail must be non-empty to reproduce the bug: `x IN (empty set)`
    # is FALSE for any x, so only a populated edge table exposes the three-valued NULL.
    con.execute("INSERT INTO raw_project_detail (managing_agency) VALUES ('DDC')")
    ingest.build_agency_dim(con)

    null_flags = con.execute(
        "SELECT count(*) FROM agency_dim WHERE cpd_active IS NULL"
    ).fetchone()[0]
    assert null_flags == 0

    # Every acronym-less agency is present (guards a meaningful test) and all FALSE.
    acronym_less = con.execute(
        "SELECT count(*), bool_and(cpd_active = FALSE) "
        "FROM agency_dim WHERE cpdw_acronym IS NULL"
    ).fetchone()
    assert acronym_less[0] > 0
    assert acronym_less[1] is True

    # The TRUE path still works: DDC appears in the edge table.
    ddc_active = con.execute(
        "SELECT cpd_active FROM agency_dim WHERE cpdw_acronym = 'DDC'"
    ).fetchone()[0]
    assert ddc_active is True


def test_atomic_swap_replaces_file(tmp_path):
    final = tmp_path / "cpd.duckdb"
    final.write_text("OLD")
    shadow = tmp_path / "cpd_shadow.duckdb"
    shadow.write_text("NEW")
    ingest.atomic_swap(shadow, final)
    assert final.read_text() == "NEW"
    assert not shadow.exists()
    assert (tmp_path / "cpd.duckdb.bak").read_text() == "OLD"
