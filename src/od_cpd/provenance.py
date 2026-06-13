# src/od_cpd/provenance.py
from __future__ import annotations


def provenance_block(*, definition: str, scope: dict, row_count: int,
                     reproduce_sql: str | None, excluded: dict | None = None,
                     as_of: dict | None = None) -> dict:
    return {
        "definition": definition,
        "scope": scope,
        "as_of": as_of or {},
        "row_count": row_count,
        "excluded": excluded or {},
        "reproduce_sql": reproduce_sql,
    }


def source_descriptor(source: str, **extra) -> dict:
    """Provenance for answers not backed by a single SQL query."""
    return {"source": source, "reproduce_sql": None, **extra}
