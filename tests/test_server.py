# tests/test_server.py
import duckdb

from od_cpd import materialize, server
from od_cpd.primer import PRIMER
from tests.test_materialize_normalized import _raw


def test_primer_states_pid_fms_and_terminology():
    assert "PID" in PRIMER and "FMS ID" in PRIMER
    assert "many-to-many" in PRIMER.lower() or "MANY-TO-MANY" in PRIMER
    assert "slippage" in PRIMER.lower()  # terminology clause present


def test_server_registers_three_tools():
    names = {t.name for t in server.mcp._tool_manager.list_tools()}
    assert {"run_sql", "dataset_info", "list_agencies"} <= names


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
    internal = {"column_dict"}  # surfaced via describe_field, not raw-SQL guidance
    doc = server.run_sql.__doc__ or ""
    missing = sorted(t for t in tables - internal if t not in doc)
    assert not missing, f"tables absent from run_sql docstring: {missing}"
