# tests/test_server.py
import anyio
import duckdb

from od_cpd import materialize, server
from od_cpd.primer import PRIMER, RULES, TOOL_RULE_IDS
from od_cpd.server import mcp
from od_cpd.table_catalog import INTERNAL_TABLES
from tests.test_materialize_normalized import _raw


def test_primer_states_pid_fms_and_terminology():
    assert "PID" in PRIMER and "FMS ID" in PRIMER
    assert "many-to-many" in PRIMER.lower() or "MANY-TO-MANY" in PRIMER
    assert "slippage" in PRIMER.lower()  # terminology clause present


def test_rule_registry_covers_tools_without_duplicate_or_orphaned_rules():
    tools = anyio.run(mcp.list_tools)
    assert set(TOOL_RULE_IDS) == {t.name for t in tools}
    assert {key for keys in TOOL_RULE_IDS.values() for key in keys} == set(RULES)
    for name, keys in TOOL_RULE_IDS.items():
        assert keys and len(keys) == len(set(keys)), name


def test_discovery_carries_critical_rules_without_server_instructions():
    descriptions = {t.name: t.description for t in anyio.run(mcp.list_tools)}
    for name in ("resolve_project_reference", "get_project_schedule", "get_project_budget",
                 "get_project_history", "project_portfolio", "rank_projects", "run_sql"):
        assert "MANY-TO-MANY" in descriptions[name], name
        assert "LIST ALL" in descriptions[name], name
        assert "reverse direction" in descriptions[name], name
    for name in ("get_project_budget", "budget_breakdown", "budget_change", "run_sql"):
        assert "Never compare budgets using fms_id alone" in descriptions[name], name
    for name in ("project_portfolio", "rank_projects", "get_project_schedule", "run_sql"):
        assert "not an allocated share" in descriptions[name], name
        assert "line_budget_total" in descriptions[name], name
    for name in ("get_project_budget", "get_project_history", "project_portfolio", "rank_projects", "run_sql"):
        assert "Do not echo loaded terms" in descriptions[name], name
        assert "suppressed" in descriptions[name], name
    for name in ("schedule_changes", "schedule_breakdown", "budget_breakdown", "run_sql"):
        assert "agency_scope" in descriptions[name], name


def test_run_sql_published_description_steers_to_typed_tables_and_grain_rules():
    # Test what an MCP client sees, including rules composed at registration.
    doc = next(t.description for t in anyio.run(mcp.list_tools) if t.name == "run_sql")
    for needle in ("latest_project_state", "budget_history", "lifetime_budget_variance",
                   "fms_sponsor", "fms_location", "original_budget",
                   "(managing_agency, fms_id)"):
        assert needle in doc, needle


def test_run_sql_docstring_covers_all_materialized_tables():
    # Drift guard: every table a caller can hit via run_sql must be named in the
    # docstring (or explicitly internal) — silent omissions undermine the steering.
    con = duckdb.connect(":memory:"); _raw(con)
    materialize.materialize_all(con)
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    doc = server.run_sql.__doc__ or ""
    missing = sorted(t for t in tables - INTERNAL_TABLES if t not in doc)
    assert not missing, f"tables absent from run_sql docstring: {missing}"


def test_every_tool_is_annotated():
    tools = anyio.run(mcp.list_tools)
    assert len(tools) == 18
    for t in tools:
        assert t.annotations is not None, t.name
        # local DuckDB is a closed world — no tool reaches outside it
        assert t.annotations.openWorldHint is False, t.name
        if t.name == "run_sql":
            # export modes write files under exports/: not read-only, not idempotent,
            # but additive — never destructive.
            assert t.annotations.readOnlyHint is False
            assert t.annotations.destructiveHint is False
            assert t.annotations.idempotentHint is False
        else:
            assert t.annotations.readOnlyHint is True, t.name
            assert t.annotations.idempotentHint is True, t.name
