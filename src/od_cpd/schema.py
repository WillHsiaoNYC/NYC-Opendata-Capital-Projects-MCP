# src/od_cpd/schema.py
from __future__ import annotations

import duckdb

# RAW column lists — verified against live /api/views (spec §5.1). All VARCHAR.
RAW_COLUMNS: dict[str, list[str]] = {
    "raw_project_detail": [
        "reporting_period", "managing_agency", "sponsor_agency", "pid", "fms_id",
        "total_budget", "spend_to_date", "spend_to_date_1", "fms_project_name",
        "agency_project_name", "agency_project_description", "current_phase",
        "current_phase_start", "forecast_current_phase_end", "forecast_completion",
        "actual_design_start", "actual_design_end", "actual_construction",
        "actual_construction_1", "actual_construction_start", "actual_construction_end",
        "borough", "community_board", "budget_line", "ten_year_plan_category",
        "agency_data_date", "fms_data_date",
    ],
    "raw_budget_fy": [
        "reporting_period", "managing_agency", "fms_id", "fiscal_year",
        "total_budget_city_non_city", "city", "non_city", "spend",
    ],
    "raw_budget_history": [
        "managing_agency", "fms_id", "year_month_reported", "total_budget",
        "spend_to_date", "spend_to_date_1", "budget_variance", "budget_variance_1",
    ],
    "raw_schedule_history": [
        "reporting_period", "managing_agency", "pid", "agency_project_name",
        "current_phase", "completion_date", "completion_date_type", "variance_day",
        "reason_for_forecast_completion_change", "data_date",
    ],
}

# Socrata dataset id -> raw table name
TABLE_FOR_DATASET: dict[str, str] = {
    "fb86-vt7u": "raw_project_detail",
    "gyhf-rsr3": "raw_budget_fy",
    "qj5n-h5qp": "raw_budget_history",
    "95tx-snak": "raw_schedule_history",
}

_META_DDL = """
CREATE TABLE meta (
    dataset_id              VARCHAR PRIMARY KEY,
    period_column           VARCHAR,
    rows_updated_at         BIGINT,
    ingest_completed_at     TIMESTAMP,
    row_count               BIGINT,
    column_hash             VARCHAR,
    schema_version          INTEGER,
    latest_reporting_period VARCHAR,
    fms_data_date           VARCHAR,
    agency_data_date        VARCHAR
);
"""

_AGENCY_DIM_DDL = """
CREATE TABLE agency_dim (
    slug                 VARCHAR,
    display_name         VARCHAR,
    aliases              VARCHAR[],
    cpdw_acronym         VARCHAR,
    cpd_active           BOOLEAN,
    is_schedule_executor BOOLEAN,
    row_count_live       BIGINT,
    role_default         VARCHAR
);
"""

SCHEMA_VERSION = 2


def _raw_ddl(table: str, columns: list[str]) -> str:
    cols = ",\n    ".join(f'"{c}" VARCHAR' for c in columns)
    return f"CREATE TABLE {table} (\n    {cols}\n);"


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    """Create all RAW + meta + agency_dim tables on a fresh connection."""
    for table, columns in RAW_COLUMNS.items():
        con.execute(_raw_ddl(table, columns))
    con.execute(_META_DDL)
    con.execute(_AGENCY_DIM_DDL)
