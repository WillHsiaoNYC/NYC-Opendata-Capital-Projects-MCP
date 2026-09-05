# tests/test_ranking.py
import duckdb
from od_cpd import schema, materialize
from od_cpd.tools.ranking import rank_projects_from
from tests.test_materialize_normalized import _raw, _orig_budget_fixture
from tests.test_agency_scope import _scope_db


def test_rank_schedule_by_period_variance_excludes_null():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = rank_projects_from(con, entity="schedule", rank_by="period_variance_days", n=10)
    assert r["ranked_entity"] == "schedule"
    assert r["rows"][0]["pid"] == "101"           # only PID with non-null variance (45)
    assert r["rows"][0]["period_variance_days"]["direction"] == "later"


def test_rank_requires_native_metric():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = rank_projects_from(con, entity="schedule", rank_by="total_budget", n=5)
    assert "error" in r   # budget metric not native to schedule entity


def test_rank_budget_sponsor_scope_finds_builder_held_line():
    con = _scope_db(duckdb.connect(":memory:"))
    r = rank_projects_from(con, entity="budget", rank_by="total_budget", agency="DOC")
    fms = [row["fms_id"] for row in r["rows"]]
    assert fms[0] == "J2"                       # $9000 DDC-held but DOC-owned, ranks first
    assert set(fms) == {"J1", "J2"}
    assert r["agency_scope"]["role"] == "sponsor"
    assert r["rows"][0]["sponsor_agencies"] == ["DOC"]   # full owner set carried per row


def test_rank_budget_managing_scope_excludes_delegated():
    con = _scope_db(duckdb.connect(":memory:"))
    r = rank_projects_from(con, entity="budget", rank_by="total_budget",
                           agency="DOC", agency_role="managing")
    assert {row["fms_id"] for row in r["rows"]} == {"J1"}   # only DOC-held


def test_rank_schedule_agency_scope():
    con = _scope_db(duckdb.connect(":memory:"))
    r = rank_projects_from(con, entity="schedule", rank_by="period_variance_days",
                           agency="DDC", n=50)
    assert r["agency_scope"]["role"] == "managing"


def test_rank_budgets_by_cumulative_change_uses_original():
    con = duckdb.connect(":memory:"); _raw(con); _orig_budget_fixture(con)
    materialize.materialize_all(con)
    r = rank_projects_from(con, entity="budget", rank_by="cumulative_budget_change", n=3)
    assert r["ranked_entity"] == "budget"
    top = r["rows"][0]
    # F: latest 1.3M - adopted 1.0M = +300k, the biggest lifetime growth in the fixture
    assert top["fms_id"] == "F"
    assert top["cumulative_budget_change"]["value"] == 300000.0
    assert top["cumulative_budget_change"]["direction"] == "increased"


def test_rank_schedule_rows_carry_agency_project_name():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    r = rank_projects_from(con, entity="schedule", rank_by="period_variance_days")
    assert r["rows"][0]["agency_project_name"] == "Park A"


def test_rank_budget_rows_carry_fms_project_name():
    con = duckdb.connect(":memory:"); _raw(con)
    con.execute("UPDATE raw_project_detail SET fms_project_name = 'FMS Park A' "
                "WHERE fms_id = 'A'")
    materialize.materialize_all(con)
    r = rank_projects_from(con, entity="budget", rank_by="total_budget")
    row_a = next(x for x in r["rows"] if x["fms_id"] == "A")
    assert row_a["fms_project_name"] == "FMS Park A"


def test_rank_budget_row_without_fb86_line_gets_null_name():
    con = duckdb.connect(":memory:"); _raw(con)
    # a budget line present ONLY in raw_budget_history (no fb86/raw_project_detail row,
    # so no fms_location entry) → the LEFT JOIN yields a NULL fms_project_name
    con.execute(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) "
        "VALUES ('QPL','QONLY','202601','5000','0','0')")
    materialize.materialize_all(con)
    r = rank_projects_from(con, entity="budget", rank_by="total_budget", n=50)
    row = next(x for x in r["rows"] if x["fms_id"] == "QONLY")
    assert row["managing_agency"] == "QPL"
    assert row["fms_project_name"] is None


def test_rank_delayed_budgets_use_current_funding_and_complete_line_keys():
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency,"
        " sponsor_agency, pid, fms_id, total_budget, current_phase)"
        " VALUES (?, ?, 'DPR', ?, ?, '100', 'Construction')",
        [
            # PID 1 replaced OLD with SHARED; matching its old line is incorrect.
            ["202509", "DDC", "1", "OLD"],
            ["202601", "DDC", "1", "SHARED"],
            # Two delayed PIDs fund the same line, which must rank only once.
            ["202601", "DDC", "2", "SHARED"],
            # A different holder's same-FMS line does not fund a delayed PID.
            ["202601", "DPR", None, "SHARED"],
            # Its latest link/schedule period predates the global latest period.
            ["202509", "DDC", "3", "OLDER"],
            # Previously delayed, currently on time: neither is a current match.
            ["202509", "DDC", "4", "RECOVERED"],
            ["202601", "DDC", "4", "RECOVERED"],
        ],
    )
    con.executemany(
        "INSERT INTO raw_schedule_history (reporting_period, managing_agency,"
        " pid, current_phase, variance_day)"
        " VALUES (?, 'DDC', ?, 'Construction', ?)",
        [
            ["202509", "1", "5"],
            ["202601", "1", "10"],
            ["202601", "2", "20"],
            ["202509", "3", "30"],
            ["202509", "4", "40"],
            ["202601", "4", "0"],
        ],
    )
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id,"
        " year_month_reported, total_budget, spend_to_date, budget_variance)"
        " VALUES (?, ?, '202601', ?, '0', '0')",
        [
            ["DDC", "OLD", "500"],
            ["DPR", "SHARED", "400"],
            ["DDC", "RECOVERED", "300"],
            ["DDC", "SHARED", "200"],
            ["DDC", "OLDER", "100"],
        ],
    )
    materialize.materialize_all(con)

    unfiltered = rank_projects_from(con, entity="budget", rank_by="total_budget")
    assert len(unfiltered["rows"]) == 5
    result = rank_projects_from(con, entity="budget", rank_by="total_budget",
                                delayed_only=True)
    assert [(r["managing_agency"], r["fms_id"], r["total_budget"])
            for r in result["rows"]] == [("DDC", "SHARED", 200), ("DDC", "OLDER", 100)]
    # The returned SQL preserves the same scope and cardinality.
    reproduced = con.execute(result["provenance"]["reproduce_sql"]).fetchall()
    assert [(r[0], r[1], r[3]) for r in reproduced] == [
        ("SHARED", "DDC", 200), ("OLDER", "DDC", 100),
    ]


def test_rank_budget_category_matches_the_complete_line_key():
    con = duckdb.connect(":memory:")
    _raw(con)
    con.execute(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) "
        "VALUES ('DPR', 'A', '202601', '1000', '0', '0')")
    materialize.materialize_all(con)
    # Explicit classifications isolate the ranking predicate from taxonomy rules.
    con.execute("""
        CREATE OR REPLACE TABLE category_dim AS
        SELECT * FROM (VALUES
            ('DDC', 'A', 'Parks & Recreation'),
            ('DPR', 'A', 'Sewer & Water'),
            ('DDC', 'B', 'Sewer & Water'),
            ('DDC', 'C', 'Parks & Recreation')
        ) AS c(managing_agency, fms_id, category)
    """)

    result = rank_projects_from(con, entity="budget", rank_by="total_budget",
                                category="Parks & Recreation")
    assert [(r["managing_agency"], r["fms_id"]) for r in result["rows"]] == [
        ("DDC", "C"), ("DDC", "A"),
    ]
