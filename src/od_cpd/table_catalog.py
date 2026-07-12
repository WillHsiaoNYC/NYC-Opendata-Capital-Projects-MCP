# src/od_cpd/table_catalog.py
"""Curated table catalog: data/tables.yaml — grain/keying notes for every
user-queryable DuckDB table. Static like data_dictionary.yaml (edit YAML, not
Python); tests/test_table_catalog.py keeps it in lockstep with what a build
actually creates. Live columns/types come from information_schema at call time
(tools/lookup.describe_table_from), never from here."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .config import data_dir


@lru_cache(maxsize=2)
def load_table_catalog(yaml_path: Path | None = None) -> dict:
    """Parse tables.yaml → {table: {kind, grain, description, keying_notes?, column_notes?}}."""
    path = yaml_path or (data_dir() / "tables.yaml")
    return yaml.safe_load(path.read_text()) or {}
