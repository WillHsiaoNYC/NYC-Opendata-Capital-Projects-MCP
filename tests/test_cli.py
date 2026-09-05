# tests/test_cli.py
from typer.testing import CliRunner
import httpx

from od_cpd import cli, socrata
from od_cpd.cli import app
from od_cpd.config import DATASETS
from tests.test_ingest_safety import seed_database

runner = CliRunner()


def test_status_reports_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv("OD_CPD_HOME", str(tmp_path))
    monkeypatch.delenv("OD_CPD_DB", raising=False)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "not initialized" in result.stdout.lower() or "run `od-cpd init`" in result.stdout


def test_init_invokes_run_ingest(monkeypatch):
    calls = {}
    monkeypatch.setattr("od_cpd.cli.run_ingest", lambda **k: calls.setdefault("n", k) or {"fb86-vt7u": 5})
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "fb86-vt7u" in result.stdout


def test_status_local_only_never_contacts_upstream(tmp_path, monkeypatch):
    path = tmp_path / "local.duckdb"
    seed_database(path)
    monkeypatch.setenv("OD_CPD_DB", str(path))
    monkeypatch.setattr(socrata, "fetch_metadata", lambda ds: (_ for _ in ()).throw(AssertionError("unexpected network")))
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "Local state only" in result.stdout
    assert "source revision=100" in result.stdout
    assert "Build:" in result.stdout


def test_status_checks_independent_revisions_and_partial_periods(tmp_path, monkeypatch):
    path = tmp_path / "local.duckdb"
    seed_database(path)
    monkeypatch.setenv("OD_CPD_DB", str(path))
    revisions = dict(zip(DATASETS, (110, 100, 90, None)))
    def metadata(ds):
        if revisions[ds] is None:
            raise httpx.ConnectError("synthetic upstream unavailable")
        return socrata.Metadata(revisions[ds], ["reporting_period"])
    monkeypatch.setattr(socrata, "fetch_metadata", metadata)
    monkeypatch.setattr(socrata, "fetch_period_counts", lambda ds: {
        "202509": 4, "202601": 4, "202605": 4, "202609": 1,
    })
    status = cli.collect_status(check_upstream=True)
    assert [info["freshness"] for info in status["datasets"].values()] == [
        "newer_revision", "up_to_date", "local_revision_newer", "upstream_unreachable",
    ]
    detail = status["datasets"][next(iter(DATASETS))]
    assert detail["latest_reporting_period"] == "202601"
    assert detail["upstream_latest_reporting_period"] == "202605"
    assert detail["upstream_latest_observed_period"] == "202609"
    assert "partial" in detail["period_warning"]
    result = runner.invoke(app, ["status", "--check-upstream"])
    assert result.exit_code == 0
    assert "upstream_unreachable" in result.stdout
    assert "up_to_date" in result.stdout
    assert "newer_revision" in result.stdout
    assert "partial coverage" in result.stdout


def test_status_does_not_call_changing_source_verified_current(tmp_path, monkeypatch):
    path = tmp_path / "local.duckdb"
    seed_database(path)
    monkeypatch.setenv("OD_CPD_DB", str(path))
    calls = {ds: 0 for ds in DATASETS}
    def metadata(ds):
        calls[ds] += 1
        return socrata.Metadata(100 + calls[ds], ["reporting_period"])
    monkeypatch.setattr(socrata, "fetch_metadata", metadata)
    monkeypatch.setattr(socrata, "fetch_period_counts", lambda ds: {"202601": 4})
    status = cli.collect_status(check_upstream=True)
    assert {info["freshness"] for info in status["datasets"].values()} == {"check_inconclusive"}


def test_update_rematerializes_changed_rules_without_download(tmp_path, monkeypatch):
    path = tmp_path / "local.duckdb"
    seed_database(path)
    monkeypatch.setenv("OD_CPD_DB", str(path))
    monkeypatch.setattr(cli, "fetch_metadata", lambda ds: socrata.Metadata(100, []))
    monkeypatch.setattr(cli.build_info, "current_fingerprint", lambda: "changed rules")
    calls = []
    monkeypatch.setattr(cli, "run_rematerialize", lambda: calls.append("rematerialize"))
    monkeypatch.setattr(cli, "run_ingest", lambda: calls.append("ingest"))
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert calls == ["rematerialize"]
    assert "without downloading" in result.stdout
