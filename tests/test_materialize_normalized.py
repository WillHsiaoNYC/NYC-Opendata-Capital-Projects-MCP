# tests/test_materialize_normalized.py
import duckdb
from od_cpd import schema, materialize


def _raw(con):
    schema.apply_schema(con)
    # fb86: PID 101 funded by 2 FMS lines (DDC); PID 102 funded by 1; a null-PID budget row
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, spend_to_date, current_phase, actual_construction_end,"
        " borough, agency_project_name) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ["202601","DDC","DDC","101","A","100","10","Construction",None,"K","Park A"],
            ["202601","DDC","DDC","101","B","50","5","Construction",None,"K","Park A"],
            ["202601","DDC","DPR","102","C","200","20","(Completed)",None,"X","Lib B"],
            ["202601","QPL",None,None,"D","300","0","(Pending)",None,"Q",None],
            # off-cadence period must be excluded
            ["202602","DDC","DDC","101","A","999","0","Construction",None,"K","Park A"],
        ],
    )
    # 95tx: variance for PID 101; PID 102 absent (completed-by-status, fb86-only)
    con.executemany(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) VALUES (?,?,?,?,?,?,?)",
        [["202601","DDC","101","Construction","2027-01-01","Forecast","45"]],
    )
    # qj5n budget history (period col = year_month_reported)
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [["DDC","A","202601","100","10","0"],
         ["DDC","A","202509","90","8","0"],
         # B and C under DDC@202601 so agency_rollup dedup = 100+50+200 = 350
         ["DDC","B","202601","50","5","0"],
         ["DDC","C","202601","200","20","0"]],
    )


def test_schedule_history_fused_and_filtered():
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    rows = con.execute(
        "SELECT pid, reporting_period, variance_day, direction, lifecycle_status "
        "FROM schedule_history ORDER BY pid").fetchall()
    # PID 101 @202601 with 95tx variance 45 → later; PID 102 (Completed) → completed; off-cadence gone
    by_pid = {r[0]: r for r in rows}
    assert set(by_pid) == {"101", "102"}
    assert by_pid["101"][2] == 45 and by_pid["101"][3] == "later"
    assert by_pid["101"][4] == "in_progress"
    assert by_pid["102"][4] == "completed"   # (Completed) phase, fb86-only, no 95tx row


def test_schedule_budget_link_cardinality():
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    rows = con.execute(
        "SELECT pid, n_budgets_for_pid FROM schedule_budget_link "
        "WHERE pid='101' AND reporting_period='202601'").fetchall()
    assert all(r[1] == 2 for r in rows)   # PID 101 funded by 2 distinct (fms_id, agency)


def test_budget_history_offcadence_excluded_and_typed():
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    periods = {r[0] for r in con.execute(
        "SELECT DISTINCT reporting_period FROM budget_history").fetchall()}
    assert periods == {"202601", "202509"}
    tb = con.execute("SELECT total_budget FROM budget_history "
                     "WHERE fms_id='A' AND reporting_period='202601'").fetchone()[0]
    assert tb == 100.0   # cast to DOUBLE


def test_borough_scalar_and_list_derived_from_lines():
    con = duckdb.connect(":memory:"); _raw(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) VALUES (?,?,?,?,?,?,?,?)",
        [
            # PID 301: two specific boroughs -> 'Multiple'
            ["202601", "DPR", "DPR", "301", "G1", "10", "Construction", "Brooklyn"],
            ["202601", "DPR", "DPR", "301", "G2", "10", "Construction", "Bronx"],
            # PID 302: one specific + Citywide -> the specific borough wins
            ["202601", "DPR", "DPR", "302", "G3", "10", "Construction", "Manhattan"],
            ["202601", "DPR", "DPR", "302", "G4", "10", "Construction", "Citywide"],
            # PID 303: Citywide only -> 'Citywide'
            ["202601", "DPR", "DPR", "303", "G5", "10", "Construction", "Citywide"],
            # PID 304: no borough at all -> NULL scalar, empty list
            ["202601", "DPR", "DPR", "304", "G6", "10", "Construction", None],
        ])
    materialize.materialize_all(con)
    rows = {r[0]: r for r in con.execute(
        "SELECT pid, borough, boroughs FROM schedule_history "
        "WHERE pid IN ('301','302','303','304')").fetchall()}
    assert rows["301"][1] == "Multiple" and rows["301"][2] == ["Bronx", "Brooklyn"]
    assert rows["302"][1] == "Manhattan" and rows["302"][2] == ["Citywide", "Manhattan"]
    assert rows["303"][1] == "Citywide" and rows["303"][2] == ["Citywide"]
    assert rows["304"][1] is None and rows["304"][2] == []
    # latest_project_state carries both forms
    lp = con.execute("SELECT borough, boroughs FROM latest_project_state "
                     "WHERE pid='301'").fetchone()
    assert lp == ("Multiple", ["Bronx", "Brooklyn"])


def test_current_phase_prefers_real_phase_over_reason():
    con = duckdb.connect(":memory:"); _raw(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) VALUES (?,?,?,?,?,?,?,?)",
        [
            # one line carries a no-schedule reason, the other the real phase
            ["202601", "DDC", "DDC", "305", "H1", "10", "(Pre-Design)", "K"],
            ["202601", "DDC", "DDC", "305", "H2", "10", "Design", "K"],
        ])
    materialize.materialize_all(con)
    phase = con.execute(
        "SELECT current_phase FROM schedule_history WHERE pid='305'").fetchone()[0]
    assert phase == "Design"


def test_current_phase_display_casing_is_canonical():
    # Upstream casing drift folds to one canonical Title-Case spelling on the DISPLAY
    # column; paren no-schedule REASONS keep their raw label.
    con = duckdb.connect(":memory:"); _raw(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) VALUES (?,?,?,?,?,?,?,?)",
        [
            ["202601", "DDC", "DDC", "401", "P1", "10", "Construction procurement", "K"],
            ["202601", "DDC", "DDC", "402", "P2", "10", "closeout", "K"],
            ["202601", "DDC", "DDC", "403", "P3", "10", "(Cancelled)", "K"],
        ])
    materialize.materialize_all(con)
    rows = dict(con.execute(
        "SELECT pid, current_phase FROM schedule_history "
        "WHERE pid IN ('401','402','403')").fetchall())
    assert rows["401"] == "Construction Procurement"
    assert rows["402"] == "Close-out"
    assert rows["403"] == "(Cancelled)"   # paren reason preserved verbatim


def _orig_budget_fixture(con):
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [
            # original-budget row: NULL spend, off-cadence pseudo-period (adoption month)
            ["DPR", "F", "201702", "1000000", None, None],
            # ordinary snapshots
            ["DPR", "F", "202509", "1200000", "100", "0"],
            ["DPR", "F", "202601", "1300000", "150", "100000"],
        ])


def test_original_budget_rows_split_from_snapshots():
    con = duckdb.connect(":memory:"); _raw(con); _orig_budget_fixture(con)
    materialize.materialize_all(con)
    # snapshot table: no NULL-spend rows, no off-cadence pseudo-periods
    assert con.execute("SELECT count(*) FROM budget_history "
                       "WHERE spend_to_date IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM budget_history "
                       "WHERE reporting_period = '201702'").fetchone()[0] == 0
    # the adopted amount lives in original_budget, off-cadence included
    assert con.execute("SELECT recorded_period, original_budget FROM original_budget "
                       "WHERE fms_id='F' AND managing_agency='DPR'").fetchone() \
        == ("201702", 1000000.0)
