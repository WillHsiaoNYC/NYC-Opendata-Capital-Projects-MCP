# src/od_cpd/tools/lookup.py
from __future__ import annotations

import duckdb

from ..agencies import SCHEDULE_EXECUTORS
from ..data_dictionary import load_dictionary
from ..dbio import rows_as_dicts
from ..primer import DOMAIN_RULES
from ..provenance import source_descriptor
from ..table_catalog import load_table_catalog
from ._common import ILIKE_ESC, LIKE_ESC, escape_like


def _table_exists(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()[0] > 0


def _columns_by_sid(con: duckdb.DuckDBPyConnection) -> dict[str, list]:
    by_sid: dict[str, list] = {}
    for c in rows_as_dicts(con, "SELECT socrata_id, field_name, display, description "
                                "FROM column_dict ORDER BY socrata_id, field_name"):
        by_sid.setdefault(c["socrata_id"], []).append(
            {"field": c["field_name"], "display": c["display"], "description": c["description"]})
    return by_sid


# dataset (raw table) -> (typed period source, in-band caveat). Periods are read from
# the TYPED tables: they are cadence-filtered and exclude qj5n's original-budget
# adoption records (any calendar month, either side of the cadence window — tracked
# in original_budget, never reporting snapshots).
_PERIOD_SOURCES = {
    "raw_project_detail": ("schedule_history", None),
    "raw_schedule_history": ("schedule_history",
        "Periods via schedule_history, whose period spine is fb86 "
        "(raw_project_detail) — identical lists today."),
    "raw_budget_history": ("budget_history",
        "Snapshot periods only. Raw rows with NULL spend_to_date are original-budget "
        "ADOPTION records stamped with any calendar month (tracked in "
        "original_budget) — not reporting snapshots, so not listed here."),
    "raw_budget_fy": ("project_budget_fy", None),
}


def dataset_info_from(con: duckdb.DuckDBPyConnection) -> dict:
    datasets = rows_as_dicts(con, """
        SELECT dataset_id, period_column, row_count, latest_reporting_period,
               rows_updated_at, ingest_completed_at, fms_data_date, agency_data_date
        FROM meta ORDER BY dataset_id
    """)
    by_sid_source = {t.get("socrata_id"): _PERIOD_SOURCES[name]
                     for name, t in load_dictionary().items()
                     if name in _PERIOD_SOURCES}
    for d in datasets:
        src = by_sid_source.get(d["dataset_id"])
        if src and _table_exists(con, src[0]):
            d["available_periods"] = [r[0] for r in con.execute(
                f"SELECT DISTINCT reporting_period FROM {src[0]} ORDER BY 1").fetchall()]
            if src[1]:
                d["period_note"] = src[1]
    caveats = [
        "Reporting periods end in 01/05/09 (Jan/May/Sep); spend reports only those periods.",
        "Null forecast dates often mean 'suppressed', not 'missing'.",
        "Some categories are filtered out upstream before publication.",
        "managing_agency = executor on schedule rows, budget-holder on budget rows.",
        "available_periods lists each dataset's reporting snapshots (from the typed "
        "tables); qj5n adoption-month original-budget records are intentionally absent.",
    ]
    if _table_exists(con, "column_dict"):
        # fold a compact field dictionary into each dataset (full detail via describe_field)
        by_sid = _columns_by_sid(con)
        for d in datasets:
            d["columns"] = by_sid.get(d["dataset_id"], [])
        caveats.append("Field definitions: per-dataset `columns` here, or call describe_field for full detail.")
    return {
        "datasets": datasets,
        "schedule_executors_count": len(SCHEDULE_EXECUTORS),
        # The full primer, embedded so clients that drop MCP server instructions
        # still receive the domain rules through the first orienting call.
        "domain_rules": DOMAIN_RULES,
        "caveats": caveats,
        "source": "meta table + column_dict",
        "reproduce_sql": None,
    }


def describe_field_from(con: duckdb.DuckDBPyConnection, field: str | None = None,
                        dataset: str | None = None) -> dict:
    """Field definitions from column_dict, optionally filtered by field (name or display)
    and/or dataset (RAW table name or socrata_id). No filter → the full dictionary."""
    where, params = [], []
    if field:
        # substring match (the docstring contract); the dataset filter below already was
        like = f"%{escape_like(field)}%"
        where.append(f"(field_name {ILIKE_ESC} OR display {ILIKE_ESC})")
        params += [like, like]
    if dataset:
        where.append(f"(table_name {ILIKE_ESC} OR socrata_id = ?)")
        params += [f"%{escape_like(dataset)}%", dataset]
    sql = ("SELECT table_name, socrata_id, field_name, display, description, key, "
           "allowed_values, limitations, notes FROM column_dict")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY table_name, field_name"
    return {
        "fields": rows_as_dicts(con, sql, params),
        "provenance": source_descriptor(
            "column_dict (data_dictionary.yaml; official NYC Open Data XLSX)"),
    }


def list_categories_from(con: duckdb.DuckDBPyConnection) -> dict:
    """Program/facility categories with budget-line counts and total budget at the
    latest period. Budget summed over budget_history rows (one per fms_id+agency),
    each tagged by its fms_id's category — mirrors the coverage measure."""
    rows = rows_as_dicts(con, """
        SELECT c.category,
               count(*) AS n_budget_lines,
               round(sum(h.total_budget), 0) AS total_budget,
               round(100.0 * sum(h.total_budget)
                     / sum(sum(h.total_budget)) OVER (), 1) AS pct_budget
        FROM budget_history h
        JOIN category_dim c USING (fms_id)
        WHERE h.reporting_period = (SELECT max(reporting_period) FROM budget_history)
        GROUP BY c.category
        ORDER BY total_budget DESC NULLS LAST
    """)
    return {
        "categories": rows,
        "provenance": source_descriptor(
            "category_dim (categories.yaml: ten-year + sponsor + fms-prefix classify)"),
    }


def list_agencies_from(con: duckdb.DuckDBPyConnection, contains: str | None = None) -> dict:
    sql = """
        SELECT slug, display_name, aliases, cpdw_acronym, cpd_active,
               is_schedule_executor, row_count_live, role_default
        FROM agency_dim
    """
    params: list = []
    if contains:
        sql += f" WHERE lower(display_name) {LIKE_ESC} OR lower(slug) {LIKE_ESC}"
        like = f"%{escape_like(contains.lower())}%"
        params = [like, like]
    sql += " ORDER BY row_count_live DESC, slug"
    agencies = rows_as_dicts(con, sql, params)
    return {
        "agencies": agencies,
        "provenance": source_descriptor("agency_dim (agencies.yaml + live intersection)"),
    }


def describe_table_from(con: duckdb.DuckDBPyConnection, table: str | None = None) -> dict:
    """Schema catalog: curated grain/keying notes (tables.yaml) + live columns/types
    (information_schema). No arg → one-line catalog; table= → full detail."""
    catalog = load_table_catalog()
    prov = source_descriptor("tables.yaml (curated) + information_schema (live)")
    if table is None:
        return {"tables": [{"table": n, "kind": e["kind"], "grain": e["grain"],
                            "description": e["description"]}
                           for n, e in catalog.items()],
                "note": "Pass table=<name> for columns/types + keying notes.",
                "provenance": prov}
    key = next((k for k in catalog if k.lower() == table.lower()), None)
    if key is None:
        return {"error": f"Unknown table '{table}'. Valid tables: "
                         f"{', '.join(sorted(catalog))}"}
    entry = catalog[key]
    cols = rows_as_dicts(con,
        "SELECT column_name AS name, data_type AS type FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position", [key])
    for c in cols:
        note = (entry.get("column_notes") or {}).get(c["name"])
        if note:
            c["note"] = note
    out = {"table": key, "kind": entry["kind"], "grain": entry["grain"],
           "description": entry["description"], "columns": cols, "provenance": prov}
    if entry.get("keying_notes"):
        out["keying_notes"] = entry["keying_notes"]
    if entry["kind"] == "raw":
        out["field_semantics"] = (f"Official field definitions: "
                                  f"describe_field(dataset='{key}')")
    return out
