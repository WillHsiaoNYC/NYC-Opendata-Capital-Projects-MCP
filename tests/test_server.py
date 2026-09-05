# tests/test_server.py
import anyio
import duckdb

from od_cpd import materialize, server
from od_cpd.primer import PRIMER
from od_cpd.server import mcp
from od_cpd.table_catalog import INTERNAL_TABLES
from tests.test_materialize_normalized import _raw


def test_primer_states_pid_fms_and_terminology():
    assert "PID" in PRIMER and "FMS ID" in PRIMER
    assert "many-to-many" in PRIMER.lower() or "MANY-TO-MANY" in PRIMER
    assert "slippage" in PRIMER.lower()  # terminology clause present


def test_run_sql_docstring_steers_to_typed_tables_and_grain_rules():
    # Gap 1: the escape hatch must carry the rules that protect raw SQL use.
    doc = server.run_sql.__doc__ or ""
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
