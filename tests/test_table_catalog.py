import duckdb

from od_cpd import materialize
from od_cpd.table_catalog import load_table_catalog
from tests.test_materialize_normalized import _raw

_INTERNAL = {"meta", "column_dict"}


def test_catalog_covers_exactly_the_built_db():
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    db_tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_type = 'BASE TABLE'").fetchall()}
    assert set(load_table_catalog()) == db_tables - _INTERNAL


def test_every_entry_has_kind_grain_description():
    for name, entry in load_table_catalog().items():
        assert entry.get("kind") in {"analytics", "dimension", "raw"}, name
        assert entry.get("grain"), name
        assert entry.get("description"), name


def test_raw_entries_point_to_describe_field():
    cat = load_table_catalog()
    raws = {n for n, e in cat.items() if e["kind"] == "raw"}
    assert raws == {"raw_project_detail", "raw_budget_fy",
                    "raw_schedule_history", "raw_budget_history"}
    for n in raws:
        assert "describe_field" in cat[n]["description"]


def test_column_notes_keys_exist_in_built_db():
    # Drift guard: every column_notes key must be a real column of its table, so a
    # renamed/removed column can't leave an orphaned note in the catalog.
    con = duckdb.connect(":memory:"); _raw(con); materialize.materialize_all(con)
    cat = load_table_catalog()
    for name, entry in cat.items():
        notes = entry.get("column_notes")
        if not notes:
            continue
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [name]).fetchall()}
        for key in notes:
            assert key in cols, f"{name}.column_notes['{key}'] is not a real column"
