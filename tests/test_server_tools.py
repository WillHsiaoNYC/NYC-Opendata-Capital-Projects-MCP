# tests/test_server_tools.py
from od_cpd import server


def test_exact_eighteen_tools_registered():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    expected = {
        "run_sql", "dataset_info", "list_agencies", "list_categories",
        "describe_field", "describe_table",
        "resolve_project_reference", "get_project_schedule", "get_project_budget",
        "get_project_history", "project_portfolio",
        "schedule_breakdown", "schedule_changes", "delay_reason_stats",
        "budget_breakdown", "budget_change", "rank_projects", "project_duration_stats",
    }
    assert names == expected
