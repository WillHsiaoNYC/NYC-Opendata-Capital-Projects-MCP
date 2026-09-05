"""Synthetic whole-ingest and rematerialization invariants; never use the live DB."""
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from od_cpd import build_info, categories, ingest, materialize, schema, socrata
from od_cpd.config import DATASETS
from od_cpd.dbio import connect_readonly


PERIODS = ("202305", "202309", "202401", "202405", "202409", "202501", "202505", "202509", "202601")


def source_rows(table, periods=PERIODS):
    rows = []
    for period in periods:
        for i in range(4):
            row = {"reporting_period": period, "year_month_reported": period,
                   "managing_agency": "DDC", "sponsor_agency": "DEP", "pid": str(100 + i),
                   "fms_id": f"WP{i}", "total_budget": "100", "spend_to_date": "10",
                   "city": "100", "non_city": "0", "spend": "10", "fiscal_year": "2027",
                   "current_phase": "Construction", "variance_day": "1",
                   "completion_date": "2027-01-01", "completion_date_type": "Forecast",
                   "borough": "Queens", "budget_line": "WP-001", "community_board": "Q01"}
            rows.append([row.get(column) for column in schema.RAW_COLUMNS[table]])
    return rows


def seed_database(path, *, periods=PERIODS, completed=True, schema_version=None):
    with duckdb.connect(str(path)) as con:
        schema.apply_schema(con)
        for ds, table in schema.TABLE_FOR_DATASET.items():
            rows = source_rows(table, periods)
            placeholders = ",".join("?" for _ in schema.RAW_COLUMNS[table])
            con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
            ingest.write_meta(con, ds, table, 100, schema.RAW_COLUMNS[table])
        if completed:
            ingest.build_agency_dim(con)
            materialize.materialize_all(con)
            con.execute("UPDATE meta SET ingest_completed_at=?", [datetime.now(timezone.utc)])
            if schema_version is not None:
                con.execute("UPDATE meta SET schema_version=?", [schema_version])


def fake_downloads(monkeypatch, *, alter=None, callback=None, periods=PERIODS):
    def download(ds, directory):
        if callback:
            callback(ds, directory)
        table = schema.TABLE_FOR_DATASET[ds]
        rows = source_rows(table, periods)
        if alter:
            rows = alter(table, rows)
        path = directory / f"{ds}.csv"
        with path.open("w", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(schema.RAW_COLUMNS[table])
            writer.writerows(rows)
        return socrata.Metadata(101, schema.RAW_COLUMNS[table]), path, len(rows)
    monkeypatch.setattr(ingest, "_download_dataset", download)


def reports(path):
    return [json.loads(report.read_text()) for report in sorted((path.parent / "ingest-runs").glob("*/report.json"))]


@pytest.fixture
def target(tmp_path, monkeypatch):
    path = tmp_path / "cpd.duckdb"
    monkeypatch.setenv("OD_CPD_DB", str(path))
    return path


def test_ingest_rejects_other_process_before_it_can_touch_run_files(target, monkeypatch):
    attempts = []
    def competing(ds, directory):
        if ds == next(iter(DATASETS)):
            attempts.append(subprocess.run(
                [sys.executable, "-c", "from od_cpd.ingest import run_ingest; run_ingest()"],
                capture_output=True, text=True, timeout=20,
            ))
    fake_downloads(monkeypatch, callback=competing)
    assert ingest.run_ingest() == {ds: 36 for ds in DATASETS}
    assert len(attempts) == 1 and attempts[0].returncode != 0
    assert "ingest already in progress" in attempts[0].stderr
    assert len(reports(target)) == 1
    assert reports(target)[0]["state"] == "published"
    assert not list((target.parent / "ingest-runs").glob("*/*.csv"))


def test_frozen_timestamp_runs_have_unique_directories_and_preserve_unrelated_files(target, monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(ingest, "datetime", FrozenDatetime)
    unrelated = target.parent / "ingest-runs" / "unrelated"
    unrelated.mkdir(parents=True)
    marker = unrelated / "source.csv"
    marker.write_text("other run")
    fake_downloads(monkeypatch)
    ingest.run_ingest()
    ingest.run_ingest()
    assert len(reports(target)) == 2
    assert marker.read_text() == "other run"
    assert reports(target)[0]["started_at"] == reports(target)[1]["started_at"]


@pytest.mark.parametrize("defect", ["duplicate", "missing_key", "missing_period", "partial_latest"])
def test_ingest_health_failure_preserves_live_and_retains_diagnostics(target, monkeypatch, defect):
    seed_database(target)
    before = target.read_bytes()
    def alter(table, rows):
        if table != "raw_schedule_history":
            return rows
        if defect == "duplicate":
            # A repeated page replaces another page: total count alone still agrees.
            return rows[:4] + rows[:4] + rows[8:]
        if defect == "missing_key":
            rows[0][schema.RAW_COLUMNS[table].index("pid")] = None
            return rows
        if defect == "missing_period":
            return rows[4:]
        return rows[:-3]
    fake_downloads(monkeypatch, alter=alter)
    with pytest.raises(ValueError):
        ingest.run_ingest()
    assert target.read_bytes() == before
    report = reports(target)[0]
    assert report["state"] == "failed"
    assert list((target.parent / "ingest-runs").glob("*/*.csv"))
    shadow = next((target.parent / "ingest-runs").glob("*/shadow.duckdb"))
    with connect_readonly(shadow) as con:
        assert not build_info.read_build_info(con)
        assert con.execute("SELECT max(schema_version), max(ingest_completed_at) FROM meta").fetchone() == (0, None)


def test_independent_complete_source_periods_are_reported_not_rejected(target):
    seed_database(target, completed=False)
    with duckdb.connect(str(target)) as con:
        con.execute("DELETE FROM raw_schedule_history WHERE reporting_period='202601'")
        health = ingest.source_health(con)
        assert health["datasets"]["95tx-snak"]["latest_reporting_period"] == "202509"
        assert health["datasets"]["fb86-vt7u"]["latest_reporting_period"] == "202601"
        assert "independent" in health["warnings"][0]


@pytest.mark.parametrize("defect", ["revision", "columns", "missing_rows", "count_changed"])
def test_download_reconciles_revision_and_counts_before_accepting(tmp_path, monkeypatch, defect):
    ds = "95tx-snak"
    before = socrata.Metadata(100, schema.RAW_COLUMNS["raw_schedule_history"])
    after = socrata.Metadata(101 if defect == "revision" else 100,
                            ["changed"] if defect == "columns" else before.columns)
    metadata = iter([before, after])
    counts = iter([3, 4 if defect == "count_changed" else 3])
    monkeypatch.setattr(socrata, "fetch_metadata", lambda ds: next(metadata))
    monkeypatch.setattr(socrata, "fetch_row_count", lambda ds: next(counts))
    def download(ds, path, **kwargs):
        assert kwargs["expected_header"] == before.columns
        path.write_text("synthetic diagnostic content")
        return 2 if defect == "missing_rows" else 3
    monkeypatch.setattr(socrata, "download_csv", download)
    with pytest.raises(ValueError):
        ingest._download_dataset(ds, tmp_path)
    assert (tmp_path / f"{ds}.csv").exists()


def test_rematerialize_supported_schema_without_network_and_preserves_source_metadata(target, monkeypatch):
    seed_database(target, schema_version=2)
    with connect_readonly(target) as con:
        before = con.execute("SELECT dataset_id, row_count, rows_updated_at, ingest_completed_at FROM meta ORDER BY 1").fetchall()
    monkeypatch.setattr(socrata, "fetch_metadata", lambda *args: pytest.fail("rematerialize must not use network"))
    report = ingest.run_rematerialize()
    assert report["state"] == "published"
    assert report["after"]["materialized_counts"]["schedule_history"] == 36
    with connect_readonly(target) as con:
        assert con.execute("SELECT dataset_id, row_count, rows_updated_at, ingest_completed_at FROM meta ORDER BY 1").fetchall() == before
        assert con.execute("SELECT min(schema_version) FROM meta").fetchone() == (schema.SCHEMA_VERSION,)
        assert build_info.read_build_info(con)["build_id"] == report["after"]["build"]["build_id"]


@pytest.mark.parametrize("defect", ["schema1", "missing_meta", "missing_raw", "bad_count", "unfinished"])
def test_rematerialize_incomplete_inputs_never_stamp_or_publish(target, defect):
    seed_database(target)
    with duckdb.connect(str(target)) as con:
        if defect == "schema1":
            con.execute("UPDATE meta SET schema_version=1")
        elif defect == "missing_meta":
            con.execute("DELETE FROM meta WHERE dataset_id='95tx-snak'")
        elif defect == "missing_raw":
            con.execute("DELETE FROM raw_schedule_history")
        elif defect == "bad_count":
            con.execute("UPDATE meta SET row_count=row_count+1")
        else:
            con.execute("UPDATE meta SET ingest_completed_at=NULL")
    before = target.read_bytes()
    with pytest.raises(ValueError):
        ingest.run_rematerialize()
    assert target.read_bytes() == before
    assert not target.with_suffix(".duckdb.bak").exists()
    assert not list((target.parent / "ingest-runs").glob("*/shadow.duckdb"))


@pytest.mark.parametrize("operation", ["ingest", "rematerialize"])
@pytest.mark.parametrize("failure", ["transient_report", "permanent_report"])
@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_published_operation_succeeds_when_completion_report_fails(
    target, monkeypatch, caplog, operation, failure, error_type,
):
    seed_database(target)
    before = target.read_bytes()
    fake_downloads(monkeypatch)
    write_report = ingest._write_report
    failures = []
    def fail_completion_report(directory, report):
        if report["state"] == "published" and (not failures or failure == "permanent_report"):
            failures.append(directory)
            raise error_type("synthetic completion report failure")
        return write_report(directory, report)
    monkeypatch.setattr(ingest, "_write_report", fail_completion_report)
    if operation == "ingest":
        assert ingest.run_ingest() == {ds: 36 for ds in DATASETS}
    else:
        returned = ingest.run_rematerialize()
        assert returned["state"] == "published"
        assert "completion report" in returned["warnings"][0]
    assert target.with_suffix(".duckdb.bak").read_bytes() == before
    with connect_readonly(target) as con:
        assert con.execute("SELECT count(*) FROM schedule_history").fetchone() == (36,)
        assert build_info.read_build_info(con)["schema_version"] == schema.SCHEMA_VERSION
    assert "Database published successfully; could not save completion report" in caplog.text
    report = reports(target)[0]
    if failure == "transient_report":
        assert report["state"] == "published"
        assert "completion report" in report["warnings"][0]
        assert len(failures) == 1
    else:
        # The last complete report remains valid and never claims rollback.
        assert report["state"] == "ready_to_publish"
        assert len(failures) == 2


def test_published_ingest_succeeds_when_csv_cleanup_fails(target, monkeypatch, caplog):
    fake_downloads(monkeypatch)
    unlink = Path.unlink
    retained = []
    def fail_one_csv(path, *args, **kwargs):
        if path.name == "95tx-snak.csv":
            retained.append(path)
            raise OSError("synthetic cleanup failure")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_one_csv)
    assert ingest.run_ingest() == {ds: 36 for ds in DATASETS}
    assert len(retained) == 1 and retained[0].exists()
    assert list(retained[0].parent.glob("*.csv")) == retained
    report = reports(target)[0]
    assert report["state"] == "published"
    assert "could not remove" in report["warnings"][0]
    assert "Database published successfully; could not remove" in caplog.text


def test_report_replacement_failure_preserves_last_complete_report(tmp_path, monkeypatch):
    ingest._write_report(tmp_path, {"state": "ready_to_publish"})
    def fail_replace(src, dst):
        raise OSError("synthetic report replacement failure")
    monkeypatch.setattr(ingest.os, "replace", fail_replace)
    with pytest.raises(OSError):
        ingest._write_report(tmp_path, {"state": "published"})
    assert json.loads((tmp_path / "report.json").read_text()) == {"state": "ready_to_publish"}


@pytest.mark.parametrize("operation", ["ingest", "rematerialize"])
def test_rules_changed_during_category_build_never_stamp_or_publish(target, monkeypatch, operation):
    seed_database(target, schema_version=2)
    before = target.read_bytes()
    with connect_readonly(target) as con:
        prior_build = build_info.read_build_info(con)
    resources = target.parent / "rules"
    resources.mkdir()
    for resource in build_info.data_dir().iterdir():
        if resource.name.endswith((".yaml", ".yml", ".tsv")):
            (resources / resource.name).write_bytes(resource.read_bytes())
    monkeypatch.setattr(build_info, "data_dir", lambda: resources)
    monkeypatch.setattr(categories, "data_dir", lambda: resources)
    category_builder = categories.build_category_dim
    def mutate_rules_after_category_build(con):
        category_builder(con)
        with (resources / "categories.yaml").open("a") as stream:
            stream.write("\n# Changed during this synthetic build.\n")
    monkeypatch.setattr(categories, "build_category_dim", mutate_rules_after_category_build)
    fake_downloads(monkeypatch)
    run = ingest.run_ingest if operation == "ingest" else ingest.run_rematerialize
    with pytest.raises(ValueError, match="rules changed during the build"):
        run()
    assert target.read_bytes() == before
    assert not target.with_suffix(".duckdb.bak").exists()
    shadow = next((target.parent / "ingest-runs").glob("*/shadow.duckdb"))
    with connect_readonly(shadow) as con:
        # Fresh ingests have no marker; copied shadows retain only the prior one.
        assert build_info.read_build_info(con) == ({} if operation == "ingest" else prior_build)
        assert con.execute("SELECT max(schema_version) FROM meta").fetchone() == (
            0 if operation == "ingest" else 2,
        )
    assert reports(target)[0]["state"] == "failed"
