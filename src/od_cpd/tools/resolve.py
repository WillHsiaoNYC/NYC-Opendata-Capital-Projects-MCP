# src/od_cpd/tools/resolve.py
from __future__ import annotations

import duckdb

from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import escape_like, interpolate_sql, validate_int


def resolve_from(con: duckdb.DuckDBPyConnection, query: str,
                 limit: int = 50, offset: int = 0) -> dict:
    error = validate_int(limit, "limit") or validate_int(offset, "offset", 0, None)
    if error:
        return error
    if not isinstance(query, str) or not query.strip():
        return {"error": "query must contain an identifier or project name."}
    query = query.strip()
    params = [query, f"%{escape_like(query.lower())}%"]
    # Match historical aliases, but display one latest nonempty name per entity.
    # Exact IDs cannot be crowded out by a large set of substring/name matches.
    schedule_base = """
        WITH needle AS (SELECT lower(?) AS exact, ? AS pattern),
        names AS (
            SELECT pid,
                   first(agency_project_name ORDER BY reporting_period DESC,
                         agency_project_name) FILTER
                       (WHERE nullif(trim(agency_project_name), '') IS NOT NULL)
                       AS agency_project_name,
                   bool_or(lower(agency_project_name) LIKE pattern ESCAPE '\\') AS name_match,
                   bool_or(lower(agency_project_description) LIKE pattern ESCAPE '\\')
                       AS description_match
            FROM schedule_history CROSS JOIN needle GROUP BY pid
        ), matched AS (
            SELECT s.pid, n.agency_project_name, s.managing_agency, s.sponsor_agency,
                   s.lifecycle_status, s.reporting_period,
                   CASE WHEN lower(s.pid) = exact THEN 0
                        WHEN lower(s.pid) LIKE pattern ESCAPE '\\' THEN 1
                        WHEN name_match THEN 2
                        WHEN description_match THEN 3 END AS match_rank
            FROM latest_project_state s JOIN names n USING (pid) CROSS JOIN needle
        ) SELECT * FROM matched WHERE match_rank IS NOT NULL
    """
    budget_base = """
        WITH needle AS (SELECT lower(?) AS exact, ? AS pattern),
        lines AS (
            SELECT fms_id, managing_agency FROM raw_project_detail
            WHERE fms_id IS NOT NULL AND managing_agency IS NOT NULL
            UNION SELECT fms_id, managing_agency FROM lifetime_budget_variance
            UNION SELECT fms_id, managing_agency FROM original_budget
        ), names AS (
            SELECT fms_id, managing_agency,
                   first(fms_project_name ORDER BY reporting_period DESC, fms_project_name)
                       FILTER (WHERE nullif(trim(fms_project_name), '') IS NOT NULL)
                       AS fms_project_name,
                   bool_or(lower(fms_project_name) LIKE pattern ESCAPE '\\') AS name_match
            FROM raw_project_detail CROSS JOIN needle GROUP BY fms_id, managing_agency
        ), matched AS (
            SELECT l.fms_id, l.managing_agency, n.fms_project_name,
                   CASE WHEN lower(l.fms_id) = exact THEN 0
                        WHEN lower(l.fms_id) LIKE pattern ESCAPE '\\' THEN 1
                        WHEN name_match THEN 2 END AS match_rank
            FROM lines l LEFT JOIN names n USING (fms_id, managing_agency)
            CROSS JOIN needle
        ) SELECT * FROM matched WHERE match_rank IS NOT NULL
    """
    buckets = {}
    provenance = {}
    pagination = {}
    for bucket, base, key, name in (
        ("schedule", schedule_base, "pid", "agency_project_name"),
        ("budget", budget_base, "fms_id", "fms_project_name"),
    ):
        matched_field = (f"CASE WHEN match_rank < 2 THEN '{key}' "
                         f"WHEN match_rank = 2 THEN '{name}' "
                         "ELSE 'agency_project_description' END")
        tie = "pid" if bucket == "schedule" else "fms_id, managing_agency"
        row_sql = (f"SELECT * EXCLUDE (match_rank), {matched_field} AS matched_field, "
                   "CASE match_rank WHEN 0 THEN 'exact_id' WHEN 1 THEN 'partial_id' "
                   "WHEN 2 THEN 'name' ELSE 'description' END AS match_type "
                   f"FROM ({base}) ORDER BY match_rank, lower({name}) NULLS LAST, {tie} "
                   f"LIMIT {limit} OFFSET {offset}")
        count_sql = f"SELECT count(*) AS total_count FROM ({base})"
        rows = rows_as_dicts(con, row_sql, params)
        total = con.execute(count_sql, params).fetchone()[0]
        buckets[f"{bucket}_matches"] = rows
        pagination[bucket] = {
            "total_count": total, "returned_count": len(rows),
            "truncated": total > len(rows),
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        }
        provenance[bucket] = provenance_block(
            definition=f"deterministic {bucket} identifier/name resolution",
            scope={"query": query, "limit": limit, "offset": offset},
            row_count=len(rows), reproduce_sql=interpolate_sql(row_sql, params),
            components={"rows": interpolate_sql(row_sql, params),
                        "total_count": interpolate_sql(count_sql, params)})
    return {
        "query": query, **buckets,
        "limit": limit, "offset": offset, "pagination": pagination,
        "note": ("Matches are ordered by exact ID, partial ID, then historical names. "
                 "Each PID or (managing_agency, fms_id) line appears once with its "
                 "latest nonempty name. Continue with the bucket's next_offset; "
                 "route schedule questions to PIDs and budget questions to FMS lines."),
        "provenance": provenance,
    }
