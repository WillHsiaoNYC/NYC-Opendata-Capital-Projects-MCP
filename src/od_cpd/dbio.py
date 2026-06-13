# src/od_cpd/dbio.py
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb

from .config import db_path
from .schema import SCHEMA_VERSION


class DBMissingError(RuntimeError):
    pass


class SchemaStaleError(DBMissingError):
    """DB was built by an older schema than the running code expects."""


def connect_readonly(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    path = path or db_path()
    if not path.exists():
        raise DBMissingError(f"DB missing at {path} — run `od-cpd init`")
    return duckdb.connect(str(path), read_only=True)


def _assert_schema_current(con: duckdb.DuckDBPyConnection) -> None:
    """Fail fast (with a clear message) when serving a DB built by an older schema —
    otherwise role-aware queries crash with a cryptic 'column role_default not found' /
    'Table fms_sponsor does not exist'. cli (connect_readonly) is intentionally NOT gated
    so `od-cpd update` can read a stale DB in order to migrate it."""
    try:
        row = con.execute("SELECT min(schema_version) FROM meta").fetchone()
    except duckdb.Error:
        return  # no/empty meta — let downstream surface the real problem
    v = row[0] if row else None
    if v is not None and v < SCHEMA_VERSION:
        raise SchemaStaleError(
            f"DB schema v{v} but code expects v{SCHEMA_VERSION} — run `od-cpd init` "
            "(or `od-cpd update`) to rebuild.")


@contextmanager
def ro_conn(path: Path | None = None):
    """Read-only connection that always closes. Raises DBMissingError if no DB,
    SchemaStaleError if the DB predates the current schema."""
    con = connect_readonly(path)
    try:
        _assert_schema_current(con)
        yield con
    finally:
        con.close()


def rows_as_dicts(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def sql_literal(value) -> str:
    """Render a Python value as a SQL literal: NULL, single-quote-escaped string, or number-as-is."""
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)
