"""Additional snapshot checks; the synthetic suite never depends on a live DB."""
from contextlib import contextmanager

import duckdb
import pytest

from od_cpd.config import db_path
from od_cpd.dbio import connect_readonly


@contextmanager
def snapshot_database(period):
    path = db_path()
    if not path.exists():
        pytest.skip("snapshot DB not present; synthetic contract coverage still runs")
    con = None
    try:
        try:
            con = connect_readonly(path)
            latest = con.execute("SELECT max(latest_reporting_period) FROM meta").fetchone()[0]
        except duckdb.Error as exc:
            pytest.skip(f"snapshot DB unreadable: {exc}")
        if latest != period:
            pytest.skip(f"DB is at {latest}, goldens pinned to {period}; see tests/evals/README.md")
        yield con
    finally:
        if con is not None:
            con.close()
