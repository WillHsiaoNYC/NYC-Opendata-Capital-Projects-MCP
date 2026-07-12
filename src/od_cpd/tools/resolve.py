# src/od_cpd/tools/resolve.py
from __future__ import annotations

import duckdb
from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import LIKE_ESC, escape_like, interpolate_sql


def resolve_from(con: duckdb.DuckDBPyConnection, query: str) -> dict:
    # escape_like: wildcards in the user's text must match literally ('50% design'
    # must not match '50<anything>design'). matched_field is computed PER ROW — a
    # constant label would claim a name match for rows that matched on id/description.
    like = f"%{escape_like(query.lower())}%"
    sched_sql = f"""
        SELECT pid, agency_project_name, managing_agency, sponsor_agency, lifecycle_status,
               CASE WHEN lower(pid) = lower(?) THEN 'pid'
                    WHEN lower(COALESCE(agency_project_name,'')) {LIKE_ESC}
                         THEN 'agency_project_name'
                    ELSE 'agency_project_description' END AS matched_field
        FROM schedule_history
        WHERE lower(pid) = lower(?)
           OR lower(COALESCE(agency_project_name,'')) {LIKE_ESC}
           OR lower(COALESCE(agency_project_description,'')) {LIKE_ESC}
        QUALIFY row_number() OVER (PARTITION BY pid ORDER BY reporting_period DESC) = 1
        LIMIT 50
    """
    budget_sql = f"""
        SELECT DISTINCT fms_id, managing_agency, fms_project_name,
               CASE WHEN lower(fms_id) = lower(?) THEN 'fms_id'
                    ELSE 'fms_project_name' END AS matched_field
        FROM raw_project_detail
        WHERE lower(fms_id) = lower(?)
           OR lower(COALESCE(fms_project_name,'')) {LIKE_ESC}
        LIMIT 50
    """
    sched_params = [query, like, query, like, like]
    budget_params = [query, query, like]
    schedule_matches = rows_as_dicts(con, sched_sql, sched_params)
    budget_matches = rows_as_dicts(con, budget_sql, budget_params)
    return {
        "query": query,
        "schedule_matches": schedule_matches,   # PIDs (route schedule questions here)
        "budget_matches": budget_matches,        # FMS lines (route budget questions here)
        "note": ("A name can fan out on the PID axis, the FMS axis, or both. "
                 "Route by question domain; list all."),
        # Per-bucket provenance: the two buckets come from independent queries against
        # different tables/grains, so each carries its OWN self-contained (single-
        # statement) reproduce_sql whose row_count covers that bucket alone. A single
        # combined block would report a summed row_count that neither query reproduces —
        # an FMS-only hit would then show a nonzero count against a schedule-only
        # reproduce_sql that returns nothing.
        "provenance": {
            "schedule": provenance_block(
                definition="PID/name match across agency_project_name, agency_project_description",
                scope={"query": query}, row_count=len(schedule_matches),
                reproduce_sql=interpolate_sql(sched_sql, sched_params)),
            "budget": provenance_block(
                definition="FMS-id/name match across fms_project_name",
                scope={"query": query}, row_count=len(budget_matches),
                reproduce_sql=interpolate_sql(budget_sql, budget_params)),
        },
    }
