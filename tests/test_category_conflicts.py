import random

import duckdb
import pytest

from od_cpd import materialize, schema
from od_cpd.tools import budget, lookup, portfolio, ranking, schedule


def _build(detail, budgets):
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    con.executemany(
        "INSERT INTO raw_project_detail (reporting_period, managing_agency, "
        "sponsor_agency, pid, fms_id, ten_year_plan_category, total_budget, current_phase) "
        "VALUES (?,?,?,?,?,?,?, 'Design')", detail)
    con.executemany(
        "INSERT INTO raw_budget_history (managing_agency, fms_id, year_month_reported, "
        "total_budget, spend_to_date) VALUES (?,?,'202601',?,'0')", budgets)
    materialize.materialize_all(con)
    return con


def _conflicts():
    # Same FMS ID, separate holders: each keeps its own program and budget.
    detail = [
        ["202601", "DCAS", None, "1", "MIX1", "ENERGY EFFICIENCY", "100"],
        ["202601", "DDC", "ACS", "2", "MIX1", "CHILD WELFARE FACILITIES", "200"],
        ["202601", "DDC", "MOR", "3", "MIX2", "ENERGY EFFICIENCY", "400"],
        ["202601", "EDC", None, "4", "MIX2", "CONSENT DECREE UPGRADING", "500"],
        # Same-line owner ties: eligible owners route by taxonomy order.
        ["202601", "DDC", "DEP", "5", "OWNERS", "NEW FACILITIES", "600"],
        ["202601", "DDC", "DOC", "5", "OWNERS", "NEW FACILITIES", "600"],
        # Same-line label ties: specific Bridges beats Energy, regardless of row order.
        ["202601", "DDC", "FDNY", "6", "LABELS", "ENERGY EFFICIENCY", "700"],
        ["202601", "DDC", "FDNY", "6", "LABELS", "PARK PEDESTRIAN BRIDGES", "700"],
        # Latest nonempty period is resolved independently for labels and owners.
        ["202305", "DDC", "DEP", "7", "HISTORY", "ENERGY EFFICIENCY", "800"],
        ["202309", "DDC", "DEP", "7", "HISTORY", None, "800"],
        ["202401", "DDC", "DHS", "7", "HISTORY", "NEW FACILITIES", "800"],
        ["202405", "DDC", "DHS", "7", "HISTORY", None, "800"],
        ["202509", "DDC", "FDNY", "7", "HISTORY", None, "800"],
        ["202601", "DDC", " , ", "7", "HISTORY", " ", "800"],
        # Institution history deliberately follows an FMS ID across holders.
        ["202305", "NYPL", "NYPL", "8", "PIN", "NEW FACILITIES", "900"],
        ["202601", "EDC", "EDC", "8", "PIN", "ENERGY EFFICIENCY", "900"],
        ["202305", "DCAS", " HHC, DCLA ", "9", "CULT", "NEW FACILITIES", "1000"],
        ["202601", "DDC", "DEP", "9", "CULT", "ENERGY EFFICIENCY", "1000"],
        # Ordinary owner history must stay on its own holder's line.
        ["202305", "DDC", "DHS", "10", "REASSIGNED", "NEW FACILITIES", "1100"],
        ["202601", "DCAS", None, "10", "REASSIGNED", "NEW FACILITIES", "1100"],
    ]
    budgets = [
        ["DCAS", "MIX1", "100"], ["DDC", "MIX1", "200"], ["EDC", "MIX1", "300"],
        ["DDC", "MIX2", "400"], ["EDC", "MIX2", "500"], ["DDC", "OWNERS", "600"],
        ["DDC", "LABELS", "700"], ["DDC", "HISTORY", "800"], ["EDC", "PIN", "900"],
        ["DDC", "CULT", "1000"], ["DCAS", "REASSIGNED", "1100"],
    ]
    return detail, budgets


def test_category_conflicts_and_totals_are_order_independent():
    expected = {
        ("DCAS", "MIX1"): "Energy & Sustainability",
        ("DDC", "MIX1"): "Social Services",
        ("EDC", "MIX1"): "Economic Development",  # budget-only, own holder fallback
        ("DDC", "MIX2"): "Energy & Sustainability",
        ("EDC", "MIX2"): "Sewer & Water",
        ("DDC", "OWNERS"): "Sewer & Water",
        ("DDC", "LABELS"): "Bridges",
        ("DDC", "HISTORY"): "Fire & EMS",
        ("NYPL", "PIN"): "Library", ("EDC", "PIN"): "Library",
        ("DCAS", "CULT"): "Cultural Institutions",
        ("DDC", "CULT"): "Cultural Institutions",
        ("DDC", "REASSIGNED"): "Homeless Shelters",
        ("DCAS", "REASSIGNED"): "City Buildings & Facilities",
    }
    baseline_totals = None
    for order in ("forward", "reverse", 17, 42, 99):
        detail, budgets = _conflicts()
        if order == "reverse":
            detail.reverse()
            budgets.reverse()
        elif isinstance(order, int):
            rng = random.Random(order)
            rng.shuffle(detail)
            rng.shuffle(budgets)
        with _build(detail, budgets) as con:
            rows = con.execute(
                "SELECT managing_agency, fms_id, category FROM category_dim").fetchall()
            assert len(rows) == len(expected)
            assert {(ma, f): cat for ma, f, cat in rows} == expected
            totals = {r["category"]: (r["value"], r["n"]) for r in
                      budget.budget_breakdown_from(con, "category")["groups"]}
            assert sum(v for v, _ in totals.values()) == 6600
            assert sum(n for _, n in totals.values()) == 11
            if baseline_totals is None:
                baseline_totals = totals
            assert totals == baseline_totals


@pytest.mark.parametrize("owners", ["HHC, DHS", "DHS,HHC", " dhs , hhc "])
def test_composite_owners_use_taxonomy_order(owners):
    with _build([["202601", "DCAS", owners, "1", "COOWNED", "NEW FACILITIES", "100"]],
                [["DCAS", "COOWNED", "100"]]) as con:
        assert con.execute("SELECT category FROM category_dim").fetchone()[0] == "Homeless Shelters"


def test_every_category_consumer_preserves_the_budget_line_key():
    with _build([
        ["202601", "DCAS", None, "11", "SHARED_ID", "ENERGY EFFICIENCY", "100"],
        ["202601", "DDC", "ACS", "12", "SHARED_ID", "CHILD WELFARE FACILITIES", "200"],
    ], [["DCAS", "SHARED_ID", "100"], ["DDC", "SHARED_ID", "200"]]) as con:
        con.execute("UPDATE latest_project_state SET period_variance_days = 10")
        discovered = {r["category"]: (r["n_budget_lines"], r["total_budget"]) for r in
                      lookup.list_categories_from(con)["categories"]}
        assert discovered == {"Energy & Sustainability": (1, 100), "Social Services": (1, 200)}
        breakdown = budget.budget_breakdown_from(con, "category")
        assert {r["category"]: r["value"] for r in breakdown["groups"]} == {
            "Energy & Sustainability": 100, "Social Services": 200}
        counts = schedule.schedule_breakdown_from(con, "category")
        assert {r["category"]: r["n"] for r in counts["groups"]} == {
            "Energy & Sustainability": 1, "Social Services": 1}
        for cat, ma, pid in [("Energy & Sustainability", "DCAS", "11"),
                             ("Social Services", "DDC", "12")]:
            ranked = ranking.rank_projects_from(con, "budget", "total_budget", category=cat)
            assert [(r["managing_agency"], r["fms_id"]) for r in ranked["rows"]] == [(ma, "SHARED_ID")]
            ranked_pids = ranking.rank_projects_from(con, "schedule", "period_variance_days", category=cat)
            assert [r["pid"] for r in ranked_pids["rows"]] == [pid]
            assert [r["pid"] for r in portfolio.project_portfolio_from(con, category=cat)["rows"]] == [pid]
        assert len(con.execute(breakdown["provenance"]["reproduce_sql"]).fetchall()) == 2
