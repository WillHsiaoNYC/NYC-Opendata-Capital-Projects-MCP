# tests/test_materialize_analytics.py
import duckdb
from od_cpd import schema, materialize
from tests.test_materialize_normalized import _orig_budget_fixture, _raw  # shared fixtures


def test_latest_project_state_lifecycle_and_attribution():
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    rows = {r[0]: r for r in con.execute(
        "SELECT pid, lifecycle_status, n_linked_budgets, attributed_budget "
        "FROM latest_project_state").fetchall()}
    assert rows["101"][1] == "in_progress"
    assert rows["101"][2] == 2                 # 2 linked budget lines
    assert rows["101"][3] == 150.0             # 100 + 50 distinct commitments
    assert rows["102"][1] == "completed"


def test_pid_funding_dedup():
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    v = con.execute("SELECT attributed_budget FROM pid_funding WHERE pid='101'").fetchone()[0]
    assert v == 150.0


def test_agency_rollup_dedups_budget_on_composite():
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    # DDC @202601: total_budget deduped over distinct (fms_id, managing_agency) = A(100)+B(50)+C(200)=350
    tb = con.execute("SELECT total_budget FROM agency_rollup_by_period "
                     "WHERE managing_agency='DDC' AND reporting_period='202601'").fetchone()[0]
    assert tb == 350.0


def test_fms_sponsor_maps_owner_and_splits_composite():
    con = duckdb.connect(":memory:"); _raw(con)
    # add a composite-sponsor PID (DDC builds; owners "DOT, DPR") + its budget line
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase) "
        "VALUES ('202601','DDC','DOT, DPR','203','E','700','Construction')")
    con.execute(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) "
        "VALUES ('DDC','E','202601','700','0','0')")
    materialize.materialize_all(con)
    pairs = set(con.execute("SELECT fms_id, sponsor_agency FROM fms_sponsor").fetchall())
    assert ("A", "DDC") in pairs and ("B", "DDC") in pairs   # PID 101 self-sponsored
    assert ("C", "DPR") in pairs                              # PID 102: DDC builds, DPR owns
    assert ("E", "DOT") in pairs and ("E", "DPR") in pairs    # composite split into atoms
    # no row carries the un-split composite string
    assert not any("," in s for _, s in pairs)


def test_fms_location_is_line_keyed_latest_row():
    con = duckdb.connect(":memory:"); _raw(con)
    # line A had borough 'M' in an older period; latest (202601) says 'K' -> latest wins.
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id,"
        " total_budget, current_phase, borough, community_board)"
        " VALUES ('202509','DDC','101','A','90','Construction','M','Manhattan 01')")
    # a line with a community board at the latest period
    con.execute(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, pid, fms_id,"
        " total_budget, current_phase, borough, community_board)"
        " VALUES ('202601','DPR',NULL,'G7','10','(Pending)','Brooklyn','Brooklyn 01')")
    materialize.materialize_all(con)
    rows = {(r[0], r[1]): (r[2], r[3]) for r in con.execute(
        "SELECT fms_id, managing_agency, borough, community_board FROM fms_location").fetchall()}
    assert rows[("A", "DDC")] == ("K", None)            # latest period wins; CB genuinely absent
    assert rows[("D", "QPL")][0] == "Q"                  # budget-only line still located
    assert rows[("G7", "DPR")] == ("Brooklyn", "Brooklyn 01")


def test_lifetime_variance_prefers_adopted_original():
    con = duckdb.connect(":memory:"); _raw(con); _orig_budget_fixture(con)
    materialize.materialize_all(con)
    f = con.execute(
        "SELECT original_budget, original_budget_source, first_snapshot_budget,"
        " latest_budget, cumulative_budget_change FROM lifetime_budget_variance"
        " WHERE fms_id='F' AND managing_agency='DPR'").fetchone()
    assert f == (1000000.0, "adopted", 1200000.0, 1300000.0, 300000.0)
    # line A has no adopted row: falls back to the first in-window snapshot (202509 = 90)
    a = con.execute(
        "SELECT original_budget, original_budget_source FROM lifetime_budget_variance"
        " WHERE fms_id='A' AND managing_agency='DDC'").fetchone()
    assert a == (90.0, "first_snapshot")
    # over_budget stays LAST-PERIOD basis by design (owner ruling 2026-06-12):
    # F's latest snapshot carries budget_variance +100000 -> True; C's is 0 -> False.
    # Cumulative growth lives in cumulative_budget_change, not this flag.
    assert con.execute("SELECT over_budget FROM lifetime_budget_variance "
                       "WHERE fms_id='F'").fetchone()[0] is True
    assert con.execute("SELECT over_budget FROM lifetime_budget_variance "
                       "WHERE fms_id='C'").fetchone()[0] is False


def test_fms_location_carries_latest_nonnull_name():
    con = duckdb.connect(":memory:"); _raw(con)
    # older period HAS a name; latest period's name is NULL — latest non-null wins
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough, fms_project_name) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [["202509","DPR","DPR","801","NM1","10","Design","Q","Old Name"],
         ["202601","DPR","DPR","801","NM1","10","Design","Q",None]])
    materialize.materialize_all(con)
    name = con.execute("SELECT fms_project_name FROM fms_location "
                       "WHERE fms_id='NM1' AND managing_agency='DPR'").fetchone()[0]
    assert name == "Old Name"


def _past_due_rows(con):
    # four PIDs at 202601: past-due; completed-by-phase; actual-completion-date; due-now
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough, forecast_completion) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [["202601","DDC","DDC","901","P1","10","Construction","K","2025-06-30"],
         ["202601","DDC","DDC","902","P2","10","Close-out","K","2025-06-30"],
         ["202601","DDC","DDC","903","P3","10","Construction","K","2025-06-30"],
         ["202601","DDC","DDC","904","P4","10","Construction","K","2026-01-15"]])
    con.execute(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency, pid,"
        " current_phase, completion_date, completion_date_type, variance_day) "
        "VALUES ('202601','DDC','903','Construction','2025-06-30','Actual','0')")


def test_forecast_past_due_definition():
    con = duckdb.connect(":memory:"); _raw(con); _past_due_rows(con)
    materialize.materialize_all(con)
    flags = dict(con.execute(
        "SELECT pid, forecast_past_due FROM latest_project_state "
        "WHERE pid IN ('901','902','903','904')").fetchall())
    assert flags["901"] is True     # in_progress, forecast 2025-06-30 < 2026-01-01
    assert flags["902"] is False    # Close-out phase → completed, NEVER past due
    assert flags["903"] is False    # completion_date_type='Actual' → completed signal
    assert flags["904"] is False    # forecast inside the snapshot month = due now


def test_forecast_past_due_never_null():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    n = con.execute("SELECT count(*) FROM latest_project_state "
                    "WHERE forecast_past_due IS NULL").fetchone()[0]
    assert n == 0
