"""Exercise the published contract through a real, isolated stdio subprocess."""
import json
import os
from pathlib import Path
import sys

import anyio
import duckdb
from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from od_cpd import materialize
from tests.test_materialize_normalized import _raw


SUCCESS_CALLS = {
    "run_sql": {"query": "SELECT DATE '2026-01-01' AS day, [1, 2] AS items"},
    "dataset_info": {},
    "list_agencies": {},
    "list_categories": {},
    "describe_field": {"field": "pid"},
    "describe_table": {"table": "schedule_source_coverage"},
    "resolve_project_reference": {"query": "101"},
    "get_project_schedule": {"pid": "101"},
    "get_project_budget": {"fms_id": "A"},
    "get_project_history": {"fms_id": "ADOPT"},
    "schedule_breakdown": {"group_by": "managing_agency"},
    "schedule_changes": {"change_type": "delayed", "from_period": "202509", "to_period": "202601"},
    "delay_reason_stats": {},
    "budget_breakdown": {},
    "budget_change": {"target": "fms:A", "from_period": "202509", "to_period": "202601"},
    "rank_projects": {"entity": "schedule", "rank_by": "cumulative_variance_days"},
    "project_duration_stats": {},
    "project_portfolio": {"n": 1},
}


def test_stdio_schemas_success_errors_and_saved_provenance(tmp_path):
    db = tmp_path / "contract.duckdb"
    with duckdb.connect(str(db)) as con:
        _raw(con)
        con.execute("INSERT INTO raw_schedule_history (pid, reporting_period, variance_day) "
                    "VALUES ('SOURCE-ONLY', '202601', '731')")
        con.execute("INSERT INTO raw_budget_history "
                    "(managing_agency, fms_id, year_month_reported, total_budget) "
                    "VALUES ('DDC', 'ADOPT', '201702', '250')")
        con.execute("INSERT INTO meta VALUES "
                    "('fb86-vt7u','reporting_period',1738000000,now(),5,'h',0,'202601',NULL,NULL)")
        materialize.materialize_all(con)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "od_cpd.server"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
             "OD_CPD_DB": str(db), "OD_CPD_HOME": str(tmp_path),
             "OD_CPD_EXPORT_DIR": str(tmp_path / "exports")},
    )

    async def exercise():
        with anyio.fail_after(60):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {t.name: t for t in (await session.list_tools()).tools}
                    assert set(tools) == set(SUCCESS_CALLS)
                    build_ids = set()
                    for name, arguments in SUCCESS_CALLS.items():
                        assert tools[name].outputSchema, name
                        result = await session.call_tool(name, arguments)
                        assert not result.isError, (name, result.content)
                        data = result.structuredContent
                        assert isinstance(data, dict), name
                        validate(data, tools[name].outputSchema)
                        assert len(result.content) == 1
                        assert json.loads(result.content[0].text) == data, name
                        build_ids.add(data["provenance"]["data_build"]["build_id"])
                        if name == "project_portfolio":
                            assert data["truncated"] and data["summary"]["n_projects"] == 2
                        if name == "get_project_history":
                            assert data["lines"][0]["periods"] == []
                            assert data["lines"][0]["original_budget"]["amount"] == 250
                    assert len(build_ids) == 1
                    for tool, arguments in (
                        ("get_project_schedule", {"pid": "missing"}),
                        ("get_project_history", {"pid": "SOURCE-ONLY", "fms_id": "A"}),
                        ("get_project_history", {"pid": "SOURCE-ONLY", "managing_agency": "DDC"}),
                        ("rank_projects", {"entity": "invalid", "rank_by": "total_budget"}),
                        ("rank_projects", {"entity": "budget", "rank_by": "period_variance_days"}),
                        ("rank_projects", {"entity": "budget", "rank_by": "total_budget", "direction": "up"}),
                        ("project_portfolio", {"population_scope": "invalid"}),
                        ("project_portfolio", {"category_scope": "invalid"}),
                        ("project_portfolio", {"agency_role": "invalid"}),
                        ("project_portfolio", {"n": 0}),
                        ("project_portfolio", {"n": 501}),
                        ("resolve_project_reference", {"query": "A", "offset": -1}),
                        ("resolve_project_reference", {"query": " "}),
                        ("describe_table", {"table": "missing"}),
                        ("delay_reason_stats", {"scope": "invalid"}),
                        ("budget_breakdown", {"agency_role": "invalid"}),
                        ("project_duration_stats", {"from_milestone": "actual_construction_end"}),
                        ("run_sql", {"query": "SELECT * FROM read_csv_auto('/tmp/missing.csv')"}),
                    ):
                        result = await session.call_tool(tool, arguments)
                        assert result.isError, (tool, arguments, result.content)
                    for name in ("get_project_schedule", "get_project_history"):
                        result = await session.call_tool(name, {"pid": "SOURCE-ONLY"})
                        assert not result.isError
                        data = result.structuredContent
                        assert data["schedule_universe"] == "dashboard_aligned"
                        assert data["source_coverage"]["source_only_rows"] == 1
                        assert data["source_periods"][0]["variance_day"] == 731
                    for output in ("csv", "xlsx"):
                        result = await session.call_tool("run_sql", {
                            "query": "SELECT '=1+1' AS text, 3 AS number", "output": output})
                        assert not result.isError, result.content
                        data = result.structuredContent
                        assert Path(data["file"]).is_file()
                        assert data["provenance"]["data_build"]["build_id"] in build_ids
                        if output == "csv":
                            saved = json.loads(Path(data["provenance_file"]).read_text())
                            assert saved == data["provenance"]
                    schema = tools["rank_projects"].inputSchema["properties"]
                    assert schema["direction"]["enum"] == ["top", "bottom"]
                    assert schema["n"]["minimum"] == 1 and schema["n"]["maximum"] == 500

    anyio.run(exercise)
