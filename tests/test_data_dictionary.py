import duckdb
from od_cpd import schema, data_dictionary as dd
from od_cpd.tools import lookup


def test_dictionary_loads_and_matches_real_schema():
    rules = dd.load_dictionary()
    assert set(rules) == {"raw_project_detail", "raw_budget_fy",
                          "raw_schedule_history", "raw_budget_history"}
    con = duckdb.connect(":memory:")
    schema.apply_schema(con)
    dd.build_column_dict(con)
    # the curated dictionary must exactly cover the live RAW schema — no drift either way
    assert dd.dictionary_drift(con) == {}
    assert con.execute("SELECT count(*) FROM column_dict").fetchone()[0] == 53


def test_pid_definition_captures_many_to_many():
    con = duckdb.connect(":memory:")
    dd.build_column_dict(con)
    out = lookup.describe_field_from(con, field="pid")
    assert {r["table_name"] for r in out["fields"]} == {"raw_project_detail", "raw_schedule_history"}
    detail = next(r for r in out["fields"] if r["table_name"] == "raw_project_detail")
    assert detail["key"] == "Foreign Key"
    assert "many-to-many" in detail["notes"].lower()


def test_describe_field_filter_by_dataset():
    con = duckdb.connect(":memory:")
    dd.build_column_dict(con)
    out = lookup.describe_field_from(con, dataset="raw_budget_history")
    fields = {r["field_name"] for r in out["fields"]}
    assert {"budget_variance", "year_month_reported"} <= fields
    assert all(r["table_name"] == "raw_budget_history" for r in out["fields"])


def test_drift_flags_undocumented_and_stale():
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE raw_x (a VARCHAR, b VARCHAR)")
    rules = {"raw_x": {"socrata_id": "x",
                       "columns": {"a": {"display": "A"}, "gone": {"display": "G"}}}}
    dd.build_column_dict(con, rules=rules)
    drift = dd.dictionary_drift(con)
    assert drift["raw_x"]["undocumented"] == ["b"]   # in the data, no definition
    assert drift["raw_x"]["stale"] == ["gone"]       # defined, not in the data
