import duckdb
import pytest

from od_cpd import materialize, schema
from od_cpd.coverage import attach_schedule_coverage, schedule_coverage


def _source_db():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    periods = ["202305", "202309", "202401", "202405", "202409", "202501", "202505"]
    con.executemany("INSERT INTO raw_project_detail (reporting_period,pid,fms_id,managing_agency,current_phase) "
                    "VALUES (?,'history','FUND','DDC','Design')", [[p] for p in periods])
    con.execute("INSERT INTO raw_project_detail (reporting_period,pid,fms_id,managing_agency,current_phase) "
                "VALUES ('202601','dashboard-only','OTHER','DDC','Design')")
    con.executemany("INSERT INTO raw_schedule_history (reporting_period,pid,managing_agency,variance_day) "
                    "VALUES (?,'history','DDC',?)", [[p, '153' if p == '202505' else '0'] for p in periods])
    con.execute("INSERT INTO raw_schedule_history (reporting_period,pid,managing_agency,variance_day) "
                "VALUES ('202601','history','DDC','731'), ('202601','source-only','DDC','25')")
    materialize.materialize_all(con)
    return con


def test_source_only_later_observations_are_retained_and_cumulative_basis_is_explicit():
    with _source_db() as con:
        total = schedule_coverage(con)
        assert (total['source_rows'], total['dashboard_rows'], total['matched_rows']) == (9, 8, 7)
        assert (total['source_only_rows'], total['dashboard_only_rows'], total['source_only_pids']) == (2, 1, 1)
        coverage = schedule_coverage(con, 'history')
        assert coverage['source_only_rows'] == 1
        assert coverage['source_latest_period'] == '202601'
        assert coverage['dashboard_latest_period'] == '202505'
        assert coverage['dashboard_cumulative_variance_days'] == 153
        assert coverage['source_cumulative_variance_days'] == 884
        assert con.execute("SELECT cumulative_variance_days FROM cumulative_schedule_variance WHERE pid='history'").fetchone() == (153,)
        assert con.execute("SELECT source_observed FROM schedule_history WHERE pid='dashboard-only'").fetchone() == (False,)
        rows = con.execute("SELECT pid, in_dashboard FROM source_schedule_history WHERE reporting_period='202601' ORDER BY pid").fetchall()
        assert rows == [('history', False), ('source-only', False)]


def test_source_only_pid_is_inspectable_and_omissions_annotate_listing_rows():
    with _source_db() as con:
        detail = attach_schedule_coverage(con, {'error': 'No dashboard schedule'}, pid='source-only', history=True)
        assert 'error' not in detail
        assert detail['periods'] == [] and detail['current_state'] is None
        assert detail['source_periods'][0]['variance_day'] == 25
        assert detail['schedule_universe'] == 'dashboard_aligned'
        assert len(con.execute(detail['provenance']['components']['source_periods']).fetchall()) == 1
        listed = attach_schedule_coverage(con, {'rows': [{'pid': 'history'}]})
        assert listed['rows'][0]['source_coverage']['source_only_rows'] == 1
        changes = attach_schedule_coverage(con, {'changes': [{'pid': 'history'}]})
        assert changes['changes'][0]['source_coverage']['source_only_rows'] == 1
        assert attach_schedule_coverage(con, {'error': 'missing'}, pid='unknown') == {'error': 'missing'}


@pytest.mark.parametrize('phase', ['(Pre-Design)', '(Design)', '(Construction)', '(Closeout)', '(Cancelled)', ' (Design)'])
def test_parenthesized_reasons_remain_distinct_from_display_phases(phase):
    with duckdb.connect(':memory:') as con:
        schema.apply_schema(con)
        con.execute("INSERT INTO raw_project_detail (reporting_period,pid,fms_id,managing_agency,current_phase) "
                    "VALUES ('202305','reason','FUND','DDC',?)", [phase])
        materialize.materialize_all(con)
        row = con.execute("SELECT current_phase,lifecycle_status FROM schedule_history").fetchone()
        assert row[0] == phase
        expected = 'completed' if phase == '(Closeout)' else 'cancelled' if phase == '(Cancelled)' else 'in_progress'
        assert row[1] == expected


def test_real_phase_wins_over_reason_across_funding_lines():
    with duckdb.connect(':memory:') as con:
        schema.apply_schema(con)
        con.execute("INSERT INTO raw_project_detail (reporting_period,pid,fms_id,managing_agency,current_phase) "
                    "VALUES ('202601','mixed','A','DDC',' (Design)'), ('202601','mixed','B','DDC','cOnStruction')")
        materialize.materialize_all(con)
        assert con.execute("SELECT current_phase FROM schedule_history").fetchone() == ('Construction',)
