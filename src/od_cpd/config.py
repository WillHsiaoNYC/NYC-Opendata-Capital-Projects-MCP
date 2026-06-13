# src/od_cpd/config.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SOCRATA_DOMAIN = "data.cityofnewyork.us"


@dataclass(frozen=True)
class Dataset:
    socrata_id: str
    name: str
    period_column: str  # the column carrying the reporting period


DATASETS: dict[str, Dataset] = {
    "fb86-vt7u": Dataset("fb86-vt7u", "Capital Project List Detail", "reporting_period"),
    "gyhf-rsr3": Dataset("gyhf-rsr3", "Budget & Spend by FY", "reporting_period"),
    "qj5n-h5qp": Dataset("qj5n-h5qp", "Budget Spend History & Variance", "year_month_reported"),
    "95tx-snak": Dataset("95tx-snak", "Schedule History & Variance", "reporting_period"),
}

# Reporting periods end in these months (Jan/May/Sep). Anything else is off-cadence.
CADENCE_MONTHS: tuple[str, ...] = ("01", "05", "09")

# A period is "full" if it has at least this fraction of the median period's row count.
FULL_PERIOD_MIN_FRACTION = 0.5

# run_sql guards
RUN_SQL_ROW_CAP = 100        # inline rows before switching to a file
RUN_SQL_TIMEOUT_SECONDS = 30

# Socrata download pagination
PAGE_SIZE = 50000


def _home() -> Path:
    """Repo-local home for runtime artifacts; overridable for tests."""
    return Path(os.environ.get("OD_CPD_HOME", Path.cwd())).expanduser()


def db_path() -> Path:
    env = os.environ.get("OD_CPD_DB")
    if env:
        return Path(env).expanduser()
    return _home() / "var" / "cpd.duckdb"


def var_dir() -> Path:
    return db_path().parent


def export_dir() -> Path:
    env = os.environ.get("OD_CPD_EXPORT_DIR")
    if env:
        return Path(env).expanduser()
    return _home() / "exports"


def app_token() -> str | None:
    return os.environ.get("OD_CPD_SOCRATA_APP_TOKEN")


def data_dir() -> Path:
    """Tracked source dictionaries (agencies.yaml, fms_agency_dim.tsv)."""
    return Path(__file__).resolve().parents[2] / "data"
