# src/od_cpd/data_dictionary.py
"""Field-definition dictionary: load data/data_dictionary.yaml (a one-time extract of
the dataset's official XLSX data dictionary) into a `column_dict` table.

Static / curated — NOT refreshed with the data. Re-extract the YAML by hand if the
upstream XLSX ever changes (it revises ~yearly). `dictionary_drift` is a build/test
guard that the curated dictionary stays in sync with the loaded table schema; it does
NOT detect upstream Socrata schema change (the RAW tables always carry exactly
schema.RAW_COLUMNS) — that signal is the per-ingest column hash (meta.column_hash).
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from .config import data_dir

_FIELDS = ("display", "description", "key", "allowed_values", "limitations", "notes")


def load_dictionary(*, yaml_path: Path | None = None) -> dict:
    """Parse data_dictionary.yaml → {table: {dataset, socrata_id, columns: {field: {...}}}}."""
    yaml_path = yaml_path or (data_dir() / "data_dictionary.yaml")
    return yaml.safe_load(yaml_path.read_text()) or {}


def build_column_dict(con: duckdb.DuckDBPyConnection, *, rules: dict | None = None) -> None:
    """CREATE OR REPLACE the column_dict table from the YAML. RAW-table independent."""
    rules = rules if rules is not None else load_dictionary()
    rows = [
        (table, t.get("socrata_id"), field, *[c.get(k) for k in _FIELDS])
        for table, t in rules.items()
        for field, c in (t.get("columns") or {}).items()
    ]
    con.execute("""
        CREATE OR REPLACE TABLE column_dict (
            table_name VARCHAR, socrata_id VARCHAR, field_name VARCHAR, display VARCHAR,
            description VARCHAR, key VARCHAR, allowed_values VARCHAR, limitations VARCHAR, notes VARCHAR
        )
    """)
    if rows:
        con.executemany("INSERT INTO column_dict VALUES (?,?,?,?,?,?,?,?,?)", rows)


def dictionary_drift(con: duckdb.DuckDBPyConnection) -> dict:
    """Build/test sync guard: per table, columns present in the loaded schema but
    undocumented, and definitions for columns no longer in the schema. Catches the
    curated YAML drifting from schema.RAW_COLUMNS — NOT an upstream source change."""
    by_table: dict[str, set[str]] = {}
    for t, f in con.execute("SELECT table_name, field_name FROM column_dict").fetchall():
        by_table.setdefault(t, set()).add(f)
    live: dict[str, set[str]] = {}
    for t, c in con.execute(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_name IN (SELECT DISTINCT table_name FROM column_dict)"
    ).fetchall():
        live.setdefault(t, set()).add(c)
    out: dict[str, dict] = {}
    for table, doc in by_table.items():
        cols = live.get(table)
        if not cols:
            continue  # table absent (e.g. partial test fixture) — nothing to compare
        undocumented, stale = sorted(cols - doc), sorted(doc - cols)
        if undocumented or stale:
            out[table] = {"undocumented": undocumented, "stale": stale}
    return out
