# src/od_cpd/ingest.py
from __future__ import annotations

import csv as _csv
import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import agencies, materialize, schema, socrata
from .config import DATASETS, db_path, var_dir
from .periods import resolve_current_period


def _assert_header_order(table: str, csv_path: Path, cols) -> None:
    """read_csv(columns={...}) maps file columns POSITIONALLY (header names are ignored),
    so an upstream column reorder would silently load every value into the wrong column.
    The all-VARCHAR raw schema would never throw — this check is the only guard."""
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        header = [h.strip().lower() for h in next(_csv.reader(fh))]
    if header != [c.lower() for c in cols]:
        raise ValueError(
            f"{table}: CSV header order does not match the expected schema — upstream "
            f"Socrata columns changed. Got {header}, expected {list(cols)}.")


def load_raw_csv(con: duckdb.DuckDBPyConnection, table: str, csv_path: Path) -> int:
    """Load a CSV into a RAW table. All columns read as VARCHAR; the file's header
    order is validated against RAW_COLUMNS first (read_csv maps positionally)."""
    cols = schema.RAW_COLUMNS[table]
    _assert_header_order(table, csv_path, cols)
    col_struct = ", ".join(f"'{c}': 'VARCHAR'" for c in cols)
    con.execute(
        f"INSERT INTO {table} BY NAME "
        f"SELECT * FROM read_csv(?, header=true, columns={{{col_struct}}}, "
        f"nullstr='', all_varchar=true)",
        [str(csv_path)],
    )
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _column_hash(columns: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(columns)).encode()).hexdigest()


def _period_counts(con: duckdb.DuckDBPyConnection, table: str, period_col: str) -> dict[str, int]:
    rows = con.execute(
        f'SELECT "{period_col}", count(*) FROM {table} '
        f'WHERE "{period_col}" IS NOT NULL GROUP BY 1'
    ).fetchall()
    return {p: c for p, c in rows}


def write_meta(con: duckdb.DuckDBPyConnection, dataset_id: str, table: str,
               rows_updated_at: int, columns: list[str]) -> None:
    ds = DATASETS[dataset_id]
    counts = _period_counts(con, table, ds.period_column)
    latest = resolve_current_period(counts)
    total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    fms_dd = agency_dd = None
    if table == "raw_project_detail" and latest:
        row = con.execute(
            "SELECT max(fms_data_date), max(agency_data_date) "
            "FROM raw_project_detail WHERE reporting_period = ?", [latest]
        ).fetchone()
        fms_dd, agency_dd = row
    con.execute(
        "INSERT INTO meta VALUES (?,?,?,?,?,?,?,?,?,?)",
        [dataset_id, ds.period_column, rows_updated_at,
         datetime.now(timezone.utc), total, _column_hash(columns),
         schema.SCHEMA_VERSION, latest, fms_dd, agency_dd],
    )


def build_agency_dim(con: duckdb.DuckDBPyConnection) -> None:
    """Insert agency rows, then mark cpd_active/row_count_live from live data."""
    rows = agencies.load_agency_rows()
    con.executemany(
        "INSERT INTO agency_dim VALUES (?,?,?,?,?,?,?,?)",
        [[r["slug"], r["display_name"], r["aliases"], r["cpdw_acronym"],
          r["cpd_active"], r["is_schedule_executor"], r["row_count_live"],
          r["role_default"]]
         for r in rows],
    )
    # Mark live presence + counts from the edge table in one set-based pass each.
    con.execute(
        "UPDATE agency_dim SET cpd_active = (cpdw_acronym IN "
        "(SELECT DISTINCT managing_agency FROM raw_project_detail))"
    )
    con.execute(
        "UPDATE agency_dim AS a SET row_count_live = t.cnt FROM ("
        "SELECT managing_agency, count(*) AS cnt FROM raw_project_detail "
        "WHERE managing_agency IS NOT NULL GROUP BY 1) t "
        "WHERE a.cpdw_acronym = t.managing_agency"
    )


def atomic_swap(shadow: Path, final: Path) -> None:
    """Replace `final` with `shadow` atomically; back up the old file."""
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        bak = final.with_suffix(final.suffix + ".bak")
        os.replace(final, bak)
    os.replace(shadow, final)


def run_ingest() -> dict:
    """Download all datasets → build shadow DB → atomic swap. Returns a summary."""
    var = var_dir()
    var.mkdir(parents=True, exist_ok=True)
    tmp = var / "tmp"
    tmp.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    shadow = var / f"cpd_{ts}.duckdb"
    con = duckdb.connect(str(shadow))
    summary: dict[str, int] = {}
    try:
        schema.apply_schema(con)
        for dataset_id, ds in DATASETS.items():
            meta = socrata.fetch_metadata(dataset_id)
            csv = tmp / f"{dataset_id}.csv"
            socrata.download_csv(dataset_id, csv)
            table = schema.TABLE_FOR_DATASET[dataset_id]
            n = load_raw_csv(con, table, csv)
            write_meta(con, dataset_id, table, meta.rows_updated_at, meta.columns)
            summary[dataset_id] = n
        build_agency_dim(con)
        materialize.materialize_all(con)
    except Exception:
        con.close()
        shadow.unlink(missing_ok=True)  # don't leave a half-built shadow DB behind
        raise
    con.close()
    atomic_swap(shadow, db_path())
    return summary
