# src/od_cpd/ingest.py
from __future__ import annotations

import csv as _csv
import errno
import hashlib
import json
import logging
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import agencies, build_info, materialize, schema, socrata
from .config import DATASETS, db_path
from .dbio import connect_readonly
from .periods import is_cadence_period, resolve_current_period


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
    snapshots = " AND spend_to_date IS NOT NULL" if table == "raw_budget_history" else ""
    rows = con.execute(
        f'SELECT "{period_col}", count(*) FROM {table} '
        f'WHERE "{period_col}" IS NOT NULL{snapshots} GROUP BY 1'
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
         None, total, _column_hash(columns),
         0, latest, fms_dd, agency_dd],
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
        "UPDATE agency_dim SET cpd_active = coalesce(cpdw_acronym IN "
        "(SELECT DISTINCT managing_agency FROM raw_project_detail), FALSE)"
    )
    con.execute(
        "UPDATE agency_dim AS a SET row_count_live = t.cnt FROM ("
        "SELECT managing_agency, count(*) AS cnt FROM raw_project_detail "
        "WHERE managing_agency IS NOT NULL GROUP BY 1) t "
        "WHERE a.cpdw_acronym = t.managing_agency"
    )


@contextmanager
def _target_lock(final: Path, operation: str):
    """Reject overlapping work; the OS releases the lock on close or exit.

    Keep the lock file in place: unlinking it could let another publisher lock a
    different inode while a previous publisher still holds the original lock.
    Ingest/rematerialize share one lock for their entire operation; publication
    also uses a short lock to coordinate direct atomic_swap callers.
    """
    suffix = "publish" if operation == "publication" else operation
    lock_path = final.with_suffix(final.suffix + f".{suffix}.lock")
    with lock_path.open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise RuntimeError(
                    f"Database {operation} already in progress for {final}; retry later."
                ) from exc
            raise
        yield


def atomic_swap(shadow: Path, final: Path) -> None:
    """Publish a completed, closed shadow on the live database's filesystem.

    The live database must be immutable (read-only readers are safe). Install a
    complete backup first without moving the live file, then replace live in one
    rename. A failure before that rename leaves live and shadow intact; .bak may
    already equal live. After success .bak is the immediately preceding image.
    Retry with the retained shadow; for rollback, copy .bak to a new shadow first.
    """
    shadow, final = shadow.resolve(), final.resolve()
    bak = final.with_suffix(final.suffix + ".bak")
    if shadow in (final, bak) or (final.exists() and shadow.samefile(final)):
        raise ValueError("Shadow must be distinct from the live database and its backup")
    if shadow.with_suffix(shadow.suffix + ".wal").exists():
        raise ValueError("Shadow database must be checkpointed and closed before publication")
    # Opening read-only validates the image and rejects an active writer. Do this
    # before touching live/backup or creating publication files.
    try:
        with connect_readonly(shadow):
            pass
    except duckdb.Error as exc:
        raise ValueError("Shadow database must be valid and closed before publication") from exc
    final.parent.mkdir(parents=True, exist_ok=True)
    with _target_lock(final, "publication"), ExitStack() as live_readers:
        if shadow.stat().st_dev != final.parent.stat().st_dev:
            raise ValueError("Shadow and live database must be on the same filesystem")
        if final.exists():
            if final.with_suffix(final.suffix + ".wal").exists():
                raise ValueError("Live database must be checkpointed and read-only before publication")
            try:
                live_readers.enter_context(connect_readonly(final))
            except duckdb.Error as exc:
                raise ValueError("Live database must be valid and read-only before publication") from exc
            # Keep a shared DuckDB read lock through backup and publication so
            # another process cannot start writing the image being backed up.
            # Also retain the copy's source descriptor: closing ANY descriptor
            # for this inode would release POSIX process-scoped DuckDB locks.
            source = live_readers.enter_context(final.open("rb"))
            with tempfile.TemporaryDirectory(
                prefix=f".{final.name}.backup-", dir=final.parent
            ) as tmp:
                staged_backup = Path(tmp) / bak.name
                with staged_backup.open("wb") as target:
                    shutil.copyfileobj(source, target)
                shutil.copystat(final, staged_backup)
                os.replace(staged_backup, bak)
        if os.name == "nt":
            # Windows cannot replace a file while our reader holds it open. All
            # writers must still follow the shadow-publication protocol.
            live_readers.close()
        os.replace(shadow, final)


def _download_dataset(dataset_id: str, tmp: Path) -> tuple[socrata.Metadata, Path, int]:
    """Download only a stable revision and reconcile its independent row counts."""
    before = socrata.fetch_metadata(dataset_id)
    if before.rows_updated_at <= 0:
        raise ValueError(f"{dataset_id}: source revision timestamp is unavailable")
    count_before = socrata.fetch_row_count(dataset_id)
    csv_path = tmp / f"{dataset_id}.csv"
    count_downloaded = socrata.download_csv(
        dataset_id, csv_path, expected_header=schema.RAW_COLUMNS[schema.TABLE_FOR_DATASET[dataset_id]]
    )
    count_after = socrata.fetch_row_count(dataset_id)
    after = socrata.fetch_metadata(dataset_id)
    if before != after:
        raise ValueError(
            f"{dataset_id}: source revision or columns changed during download "
            f"({before.rows_updated_at} -> {after.rows_updated_at}; columns_changed={before.columns != after.columns})"
        )
    if count_before <= 0 or not (count_before == count_downloaded == count_after):
        raise ValueError(
            f"{dataset_id}: source row counts disagree: before={count_before}, "
            f"downloaded={count_downloaded}, after={count_after}"
        )
    return after, csv_path, count_downloaded


_SOURCE_KEYS = {
    "raw_project_detail": "reporting_period, managing_agency, fms_id, pid, budget_line, borough, community_board",
    "raw_budget_fy": "reporting_period, managing_agency, fms_id, fiscal_year",
    # Adopted amounts and snapshots are separate legitimate rows in one month.
    "raw_budget_history": "managing_agency, fms_id, year_month_reported, (spend_to_date IS NULL)",
    "raw_schedule_history": "reporting_period, pid",
}


def source_health(con: duckdb.DuckDBPyConnection, *, expected_counts: dict | None = None,
                  previous: dict | None = None) -> dict:
    """Validate complete raw inputs and report each source's independent coverage.

    No source is required to share another source's period list or row universe.
    Missing prior snapshots, empty/partial latest snapshots and invalid/duplicate
    source keys block publication rather than being hidden by aggregation.
    """
    result = {"datasets": {}, "warnings": []}
    for ds, dataset in DATASETS.items():
        table = schema.TABLE_FOR_DATASET[ds]
        columns = con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=? ORDER BY ordinal_position", [table]
        ).fetchall()
        if [row[0] for row in columns] != schema.RAW_COLUMNS[table]:
            raise ValueError(f"{ds}: raw columns do not match the supported schema")
        count = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        if count == 0 or (expected_counts is not None and count != expected_counts.get(ds)):
            raise ValueError(f"{ds}: raw row count {count} is empty or does not match the source")
        required = [dataset.period_column, "managing_agency",
                    "pid" if table == "raw_schedule_history" else "fms_id"]
        if table == "raw_budget_fy":
            required.append("fiscal_year")
        bad_key = " OR ".join(f"{col} IS NULL OR trim({col})=''" for col in required)
        if "pid" in schema.RAW_COLUMNS[table]:
            bad_key += " OR (pid IS NOT NULL AND (try_cast(pid AS BIGINT) IS NULL OR try_cast(pid AS BIGINT)<=0))"
        if table == "raw_budget_fy":
            bad_key += " OR try_cast(fiscal_year AS INTEGER) IS NULL"
        if con.execute(f"SELECT count(*) FROM {table} WHERE {bad_key}").fetchone()[0]:
            raise ValueError(f"{ds}: missing or invalid source key")
        period = dataset.period_column
        invalid_periods = con.execute(
            f"SELECT count(*) FROM {table} WHERE NOT regexp_full_match({period}, "
            "'[1-9][0-9]{3}(0[1-9]|1[0-2])')"
        ).fetchone()[0]
        counts = _period_counts(con, table, period)
        if invalid_periods or any(not is_cadence_period(p) for p in counts):
            raise ValueError(f"{ds}: invalid snapshot reporting period")
        duplicates = con.execute(
            f"SELECT count(*) FROM (SELECT {_SOURCE_KEYS[table]}, count(*) AS n "
            f"FROM {table} GROUP BY ALL HAVING n>1)"
        ).fetchone()[0]
        if duplicates:
            raise ValueError(f"{ds}: {duplicates} duplicate source key groups")
        latest = resolve_current_period(counts)
        if latest is None or latest != max(counts):
            raise ValueError(f"{ds}: latest snapshot is empty or has partial period coverage")
        prior = (previous or {}).get("datasets", {}).get(ds, {})
        missing = sorted(set(prior.get("period_counts", {})) - set(counts))
        if missing:
            raise ValueError(f"{ds}: previously published snapshot periods disappeared: {missing}")
        result["datasets"][ds] = {"row_count": count, "period_counts": counts,
                                  "latest_reporting_period": latest,
                                  "row_count_delta": count - prior.get("row_count", count)}
    latest_periods = {ds: info["latest_reporting_period"] for ds, info in result["datasets"].items()}
    if len(set(latest_periods.values())) > 1:
        result["warnings"].append(
            f"Sources have different latest reporting periods; their populations remain independent: {latest_periods}"
        )
    return result


def _local_health(final: Path) -> dict:
    if not final.exists():
        return {}
    with connect_readonly(final) as con:
        try:
            return source_health(con)
        except (duckdb.Error, ValueError) as exc:
            return {"warnings": [f"Prior source health unavailable: {exc}"]}


def _run_directory(final: Path, operation: str) -> Path:
    runs = final.parent / "ingest-runs"
    runs.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{final.stem}-{operation}-", dir=runs))


def _write_report(directory: Path, report: dict) -> None:
    staged = directory / ".report.json.tmp"
    staged.write_text(json.dumps(report, indent=2, default=str) + "\n")
    os.replace(staged, directory / "report.json")


def _finish_publication(directory: Path, report: dict, downloads=()) -> None:
    """The database is committed; bookkeeping failures must not imply rollback."""
    report.update(state="published", completed_at=datetime.now(timezone.utc))
    messages = report.setdefault("warnings", [])
    for path in downloads:
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            messages.append(f"Database published successfully; could not remove {path}: {exc}")
    try:
        _write_report(directory, report)
    except Exception as exc:
        messages.append(f"Database published successfully; could not save completion report at {directory}: {exc}")
        # One retry can preserve both the committed state and a transient I/O
        # warning. A permanent failure leaves the previous atomic report intact.
        try:
            _write_report(directory, report)
        except Exception:
            pass
    for message in messages:
        logging.getLogger(__name__).warning(message)


def _completed_health(con: duckdb.DuckDBPyConnection, health: dict) -> dict:
    build = build_info.read_build_info(con)
    if not build or build["schema_version"] != schema.SCHEMA_VERSION:
        raise ValueError("Materialization did not produce a completed build identity")
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    required = {"schedule_history", "budget_history", "project_budget_fy", "latest_project_state", "category_dim"}
    if not required <= tables:
        raise ValueError(f"Materialization is missing required tables: {sorted(required - tables)}")
    health["materialized_counts"] = {
        table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in ("schedule_history", "source_schedule_history", "budget_history",
                      "project_budget_fy", "latest_project_state", "category_dim") if table in tables
    }
    health["build"] = build
    return health


def run_ingest() -> dict:
    """Validate stable full downloads, build in isolation, then publish once."""
    final = db_path().resolve()
    final.parent.mkdir(parents=True, exist_ok=True)
    with _target_lock(final, "ingest"):
        directory = _run_directory(final, "ingest")
        shadow = directory / "shadow.duckdb"
        report = {"operation": "ingest", "started_at": datetime.now(timezone.utc),
                  "state": "downloading", "before": _local_health(final)}
        _write_report(directory, report)
        try:
            with ThreadPoolExecutor(max_workers=len(DATASETS)) as ex:
                downloads = dict(zip(DATASETS, ex.map(
                    lambda ds: _download_dataset(ds, directory), DATASETS)))
            report["state"] = "building"
            with duckdb.connect(str(shadow)) as con:
                schema.apply_schema(con)
                summary = {}
                for ds, (meta, csv_path, _count) in downloads.items():
                    table = schema.TABLE_FOR_DATASET[ds]
                    summary[ds] = load_raw_csv(con, table, csv_path)
                    write_meta(con, ds, table, meta.rows_updated_at, meta.columns)
                health = source_health(con, expected_counts={ds: item[2] for ds, item in downloads.items()},
                                       previous=report["before"])
                build_agency_dim(con)
                materialize.materialize_all(con)
                con.execute("UPDATE meta SET ingest_completed_at=?", [datetime.now(timezone.utc)])
                report["after"] = _completed_health(con, health)
            report["state"] = "ready_to_publish"
            _write_report(directory, report)
            atomic_swap(shadow, final)
        except Exception as exc:
            report.update(state="failed", error=str(exc))
            _write_report(directory, report)
            exc.add_note(f"Ingest diagnostics retained at {directory}")
            raise
        _finish_publication(directory, report, (item[1] for item in downloads.values()))
        return summary


def run_rematerialize() -> dict:
    """Rebuild supported, complete raw inputs in a shadow without network access."""
    final = db_path().resolve()
    final.parent.mkdir(parents=True, exist_ok=True)
    with _target_lock(final, "ingest"):
        directory = _run_directory(final, "rematerialize")
        shadow = directory / "shadow.duckdb"
        report = {"operation": "rematerialize", "started_at": datetime.now(timezone.utc),
                  "state": "validating"}
        try:
            if final.with_suffix(final.suffix + ".wal").exists():
                raise ValueError("Live database must be checkpointed and read-only before rematerialization")
            with connect_readonly(final) as source:
                metadata = source.execute(
                    "SELECT dataset_id, row_count, schema_version, ingest_completed_at, rows_updated_at FROM meta"
                ).fetchall()
                if ({row[0] for row in metadata} != set(DATASETS)
                        or any(row[2] is None or not 2 <= row[2] <= schema.SCHEMA_VERSION
                               or row[3] is None or not row[4] for row in metadata)):
                    raise ValueError("Rematerialization requires complete source metadata from schema 2 or later; run init")
                health = source_health(source, expected_counts={row[0]: row[1] for row in metadata})
                report["before"] = {**health, "build": build_info.read_build_info(source)}
                # Keep the copy descriptor open until its source reader is no longer needed.
                with final.open("rb") as src, shadow.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
            report["state"] = "building"
            with duckdb.connect(str(shadow)) as con:
                con.execute("DROP TABLE IF EXISTS agency_dim")
                con.execute(schema._AGENCY_DIM_DDL)
                build_agency_dim(con)
                materialize.materialize_all(con)
                report["after"] = _completed_health(con, health)
            report["state"] = "ready_to_publish"
            _write_report(directory, report)
            atomic_swap(shadow, final)
        except Exception as exc:
            report.update(state="failed", error=str(exc))
            _write_report(directory, report)
            exc.add_note(f"Rematerialization diagnostics retained at {directory}")
            raise
        _finish_publication(directory, report)
        return report
