# tests/test_cli.py
from typer.testing import CliRunner

from od_cpd.cli import app

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
