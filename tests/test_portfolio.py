# tests/test_portfolio.py — gap 3: the portfolio cross-section tool.
from od_cpd import materialize
from od_cpd.tools.portfolio import project_portfolio_from
from tests.test_category_groupby import _category_db


def _portfolio_db():
    """_category_db plus: PID 504 SHARES line LB2 with 502 (dedup case), and
    PID 505 is genuinely multi-borough (Brooklyn + Bronx lines)."""
    con = _category_db()
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough) VALUES (?,?,?,?,?,?,?,?)",
        [["202601", "DPR", "DPR", "504", "LB2", "200", "Design", "Q"],
         ["202601", "DPR", "DPR", "505", "P-BK1", "30", "Construction", "Brooklyn"],
         ["202601", "DPR", "DPR", "505", "P-BX1", "40", "Construction", "Bronx"]])
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported,"
        " total_budget, spend_to_date, budget_variance) VALUES (?,?,?,?,?,?)",
        [["DPR", "P-BK1", "202601", "30", "1", "0"],
         ["DPR", "P-BX1", "202601", "40", "2", "0"]])
    materialize.materialize_all(con)
    return con


def test_portfolio_category_filter_with_dual_budget_bases():
    con = _portfolio_db()
    r = project_portfolio_from(con, category="Library")
    s = r["summary"]
    assert s["n_projects"] == 3                       # 501, 502, 504
    # per-PID attribution counts the shared LB2 line on BOTH 502 and 504...
    assert s["attributed_budget_total"] == 550.0      # 501:150 + 502:200 + 504:200
    # ...while the line-level total dedups it (LB1 100 + HB1 50 + LB2 200)
    assert s["line_budget_total"] == 350.0
    assert {row["pid"] for row in r["rows"]} == {"501", "502", "504"}


def test_portfolio_borough_filter_matches_the_list():
    con = _portfolio_db()
    r = project_portfolio_from(con, borough="Bronx")
    assert {row["pid"] for row in r["rows"]} == {"505"}   # found via its boroughs LIST
    assert r["rows"][0]["borough"] == "Multiple"
    assert r["rows"][0]["boroughs"] == ["Bronx", "Brooklyn"]


def test_portfolio_lifecycle_validation_and_notes():
    con = _portfolio_db()
    assert "error" in project_portfolio_from(con, lifecycle_status="nope")
    r = project_portfolio_from(con, category="Library", lifecycle_status="in_progress")
    assert r["summary"]["n_projects"] == 3
    notes = " ".join(r["notes"])
    assert "attributed_budget" in notes and "EACH" in notes  # bases + count-in-each


def _cb_stale_link_db():
    """PID 601 is funded by line CBX (community board 'X') in an OLD period only —
    that line is DROPPED by the latest period, where 601 is funded by line CBY
    (community board 'Y'). schedule_budget_link is all-history, so the CB filter
    must match 601's CURRENT (latest link-period) lines, not the stale 'X' tie."""
    con = _category_db()
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, sponsor_agency,"
        " pid, fms_id, total_budget, current_phase, borough, community_board)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [["202509", "DPR", "DPR", "601", "CBX", "10", "Construction", "Q", "X"],
         ["202601", "DPR", "DPR", "601", "CBY", "10", "Construction", "Q", "Y"]])
    materialize.materialize_all(con)
    return con


def test_portfolio_community_board_filter_uses_current_links_only():
    con = _cb_stale_link_db()
    # 'X' is a DROPPED (old-period) funding line for 601 -> must NOT resurface it.
    rx = project_portfolio_from(con, community_board="X")
    assert {row["pid"] for row in rx["rows"]} == set()
    # 'Y' is 601's CURRENT (latest link-period) funding line -> must match.
    ry = project_portfolio_from(con, community_board="Y")
    assert {row["pid"] for row in ry["rows"]} == {"601"}
