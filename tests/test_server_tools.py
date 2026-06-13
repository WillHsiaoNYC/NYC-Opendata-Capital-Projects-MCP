# tests/test_server_tools.py
from od_cpd import server


def test_all_thirteen_tools_registered():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    expected = {
        "run_sql", "dataset_info", "list_agencies",                  # Plan 1
        "resolve_project_reference", "get_project_schedule", "get_project_budget",
        "schedule_breakdown", "schedule_changes", "delay_reason_stats",
        "budget_breakdown", "budget_change", "rank_projects", "project_duration_stats",
    }
    assert expected <= names
