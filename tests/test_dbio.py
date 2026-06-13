# tests/test_dbio.py
import duckdb
import pytest

from od_cpd import dbio, schema


def _meta(con, version):
    con.execute("CREATE TABLE meta (dataset_id VARCHAR, schema_version INTEGER)")
    con.execute("INSERT INTO meta VALUES ('x', ?)", [version])


def test_schema_stale_raises():
    con = duckdb.connect(":memory:")
    _meta(con, schema.SCHEMA_VERSION - 1)
    with pytest.raises(dbio.SchemaStaleError):
        dbio._assert_schema_current(con)


def test_schema_current_passes():
    con = duckdb.connect(":memory:")
    _meta(con, schema.SCHEMA_VERSION)
    dbio._assert_schema_current(con)  # no raise


def test_schema_check_tolerates_missing_meta():
    # No meta table at all → don't crash here; let downstream surface the real issue.
    con = duckdb.connect(":memory:")
    dbio._assert_schema_current(con)


def test_schema_stale_error_is_db_missing_subclass():
    # so server._with_conn's `except DBMissingError` returns a clean error dict
    assert issubclass(dbio.SchemaStaleError, dbio.DBMissingError)
