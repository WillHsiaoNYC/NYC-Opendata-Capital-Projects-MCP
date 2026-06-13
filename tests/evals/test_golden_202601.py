# tests/evals/test_golden_202601.py — goldens pinned to the 202601 snapshot.
import duckdb
import pytest

from od_cpd.config import db_path
from od_cpd.dbio import connect_readonly
from od_cpd.tools.inspect import get_project_budget_from, get_project_schedule_from
from od_cpd.tools.lookup import dataset_info_from
from od_cpd.tools.portfolio import project_portfolio_from
from od_cpd.tools.ranking import rank_projects_from
from od_cpd.tools.schedule import delay_reason_stats_from, schedule_breakdown_from

GOLDEN_PERIOD = "202601"


@pytest.fixture(scope="module")
def con():
    # All gating lives here so importing/collecting the module has no side effects.
    path = db_path()
    if not path.exists():
        pytest.skip("live DB not present")
    try:
        c = connect_readonly(path)
        latest = c.execute(
            "SELECT max(latest_reporting_period) FROM meta").fetchone()[0]
    except duckdb.Error as e:
        pytest.skip(f"live DB unreadable: {e}")
    if latest != GOLDEN_PERIOD:
        c.close()
        pytest.skip(f"DB is at {latest}, goldens pinned to {GOLDEN_PERIOD} — re-pin "
                    "(see tests/evals/README.md)")
    yield c
    c.close()


def test_dataset_info_orients_with_rules(con):
    info = dataset_info_from(con)
    assert all(d["latest_reporting_period"] == GOLDEN_PERIOD for d in info["datasets"])
    assert any("MANY-TO-MANY" in r for r in info["domain_rules"])


def test_doc_biggest_budget_is_bbjq_with_role_and_coowners(con):
    # Golden 2026-06-09: "most budgeted DOC project" — the question that drove
    # role-aware attribution. Sponsor lens must auto-apply; co-owners visible.
    r = rank_projects_from(con, entity="budget", rank_by="total_budget", n=1,
                           agency="DOC")
    top = r["rows"][0]
    assert top["fms_id"] == "BBJ-Q"
    assert top["total_budget"] == pytest.approx(4_474_638_690.85)
    assert top["sponsor_agencies"] == ["DEP", "DOC"]          # multi-sponsor listed
    assert r["agency_scope"]["role"] == "sponsor"
    assert "do not sum across agencies" in r["agency_scope"]["note"]


def test_biggest_parks_and_library_lines(con):
    # Golden 2026-06-08: Brooklyn Bridge Park $288.0M; Central Renovation Ph 2A $56.44M.
    parks = rank_projects_from(con, entity="budget", rank_by="total_budget", n=1,
                               category="Parks & Recreation")["rows"][0]
    assert parks["fms_id"] == "P-202018A"
    assert parks["total_budget"] == pytest.approx(287_998_855.71)
    lib = rank_projects_from(con, entity="budget", rank_by="total_budget", n=1,
                             category="Library")["rows"][0]
    assert lib["fms_id"] == "LBCENPH2"
    assert lib["total_budget"] == pytest.approx(56_438_166.0)


def test_po79lock_fans_out_and_says_so(con):
    # Golden 2026-06-08: PO79LOCK funds 3 PIDs — the M:M list-all rule.
    b = get_project_budget_from(con, "PO79LOCK")
    assert len(b["linked_schedules"]) == 3
    assert "never collapse" in b["caveat"]


def test_multi_borough_pid_lists_all(con):
    # Catch Basin Modernization: the lineage-keyed location rule end to end.
    s = get_project_schedule_from(con, "4752")["answer"]
    assert s["borough"] == "Multiple"
    assert s["boroughs"] == ["Bronx", "Citywide", "Queens"]


def test_bbjq_growth_states_its_basis(con):
    # Golden 2026-06-12 (corrected): adopted $2.114B -> latest $4.475B.
    row = get_project_budget_from(con, "BBJ-Q")["answer"][0]
    assert row["original_budget"] == pytest.approx(2_113_874_000.0)
    assert row["original_budget_source"] == "adopted"
    assert row["cumulative_budget_change"] == pytest.approx(
        row["latest_budget"] - row["original_budget"])


def test_dpr_top_delay_reason_is_permits(con):
    # Golden 2026-06-09: permits lead DPR's delay reasons (58 of 108 then).
    # The golden is the RANKING, not the raw count — counts churn on re-ingest.
    reasons = delay_reason_stats_from(con, agency="DPR")["reasons"]
    assert "PERMIT" in reasons[0]["reason_for_delay"].upper()
    assert reasons[0]["n"] > reasons[1]["n"]


def test_borough_breakdown_has_multiple_bucket_and_note(con):
    r = schedule_breakdown_from(con, group_by="borough")
    buckets = {g["borough"]: g["n"] for g in r["groups"]}
    # presence is the invariant — a list-derivation regression would zero it out
    assert buckets.get("Multiple", 0) >= 1
    assert "'Multiple' = lines in 2+ specific boroughs" in r["label"]


def test_portfolio_replays_the_library_listing(con):
    # Golden 2026-06-09: in-progress library projects, nearest completion Red Hook.
    r = project_portfolio_from(con, category="Library",
                               lifecycle_status="in_progress", n=3)
    first = r["rows"][0]
    assert first["pid"] == "66" and "Red Hook" in first["agency_project_name"]
    assert r["truncated"] is True
    assert any("line_budget_total" in n for n in r["notes"])


def test_cumulative_rank_envelope_shape(con):
    # Shape-only live-DB layer over the unit test: signed envelope + positive
    # lifetime growth at the top.
    top = rank_projects_from(con, entity="budget",
                             rank_by="cumulative_budget_change", n=1)["rows"][0]
    env = top["cumulative_budget_change"]
    assert env["direction"] == "increased" and env["value"] > 1e9
