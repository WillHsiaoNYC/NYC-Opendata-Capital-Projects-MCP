# src/od_cpd/provenance.py
from __future__ import annotations

from .build_info import read_build_info


def provenance_block(*, definition: str, scope: dict, row_count: int,
                     reproduce_sql: str | None, excluded: dict | None = None,
                     as_of: dict | None = None,
                     components: dict[str, str] | None = None) -> dict:
    result = {
        "definition": definition,
        "scope": scope,
        "as_of": as_of or {},
        "row_count": row_count,
        "excluded": excluded or {},
        "reproduce_sql": reproduce_sql,
    }
    if components is not None:
        result["components"] = components
    return result


def source_descriptor(source: str, **extra) -> dict:
    """Provenance for answers not backed by a single SQL query."""
    return {"source": source, "reproduce_sql": None, **extra}


def enrich_provenance(con, result: dict) -> dict:
    """Identify the completed data build on every MCP answer and saved export."""
    if "error" in result:
        return result
    provenance = result.setdefault("provenance", {})
    build = read_build_info(con)
    provenance["data_build"] = build or {"available": False, "note": "Database has no completed build identity."}
    periods = {ds: source.get("latest_reporting_period")
               for ds, source in build.get("source_revisions", {}).items()}
    provenance["as_of"] = {"source_reporting_periods": periods,
                           **provenance.get("as_of", {})}
    if result.get("period") is not None:
        provenance["as_of"]["selected_period"] = result["period"]
    if result.get("current_period") is not None:
        provenance["as_of"]["current_period"] = result["current_period"]
    return result
