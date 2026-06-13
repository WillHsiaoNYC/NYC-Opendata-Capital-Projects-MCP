# tests/test_config.py
from od_cpd import config


def test_four_datasets_with_period_columns():
    ds = config.DATASETS
    assert set(ds) == {"fb86-vt7u", "gyhf-rsr3", "qj5n-h5qp", "95tx-snak"}
    # qj5n's period column differs from the rest
    assert ds["qj5n-h5qp"].period_column == "year_month_reported"
    assert ds["fb86-vt7u"].period_column == "reporting_period"


def test_cadence_months():
    assert config.CADENCE_MONTHS == ("01", "05", "09")


def test_db_path_defaults_into_var(tmp_path, monkeypatch):
    monkeypatch.delenv("OD_CPD_DB", raising=False)
    monkeypatch.setenv("OD_CPD_HOME", str(tmp_path))
    assert config.db_path() == tmp_path / "var" / "cpd.duckdb"
    assert config.export_dir() == tmp_path / "exports"
