# src/od_cpd/tools/sql.py
from __future__ import annotations

import re
import threading

import duckdb

from ..config import RUN_SQL_ROW_CAP, RUN_SQL_TIMEOUT_SECONDS
from ..provenance import provenance_block

# Defense-in-depth only. The AUTHORITATIVE write guard is the read-only DuckDB
# connection (dbio.connect_readonly opens with read_only=True), which rejects any
# mutation at the engine level. This keyword check just fails fast with a clear
# message; the prefix check below already blocks non-SELECT/WITH statements.
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|COPY|"
    r"INSTALL|LOAD|SET|CALL|EXPORT|IMPORT)\b",
    re.IGNORECASE,
)

# String literals ('it''s') and comments (-- … / /* … */): blanked before the keyword
# and single-statement checks, so a literal like '%update%' or 'a;b' is not a false hit.
_LITERAL_OR_COMMENT = re.compile(r"'(?:[^']|'')*'|--[^\n]*|/\*.*?\*/", re.DOTALL)

# Dimensions with one latest-row-per-entity and NO reporting_period column. Counting
# them yields the all-history universe, not a single period — the trap that makes a
# borough/owner inventory look period-scoped when it isn't.
_ALL_HISTORY_TABLES = ("fms_location", "fms_sponsor", "lifetime_budget_variance")


def validate_select(query: str) -> str:
    """Return the cleaned query if it is a single read-only SELECT/WITH; else raise."""
    q = query.strip().rstrip(";").strip()
    scannable = _LITERAL_OR_COMMENT.sub(" ", q)
    if ";" in scannable:
        raise ValueError("Only a single statement is allowed.")
    if not re.match(r"(?is)^\s*(SELECT|WITH)\b", q):
        raise ValueError("Only SELECT/WITH queries are allowed.")
    if _FORBIDDEN.search(scannable):
        raise ValueError("Query contains a forbidden keyword (read-only only).")
    return q


def _interrupt_after(con: duckdb.DuckDBPyConnection, seconds: int) -> threading.Timer:
    t = threading.Timer(seconds, con.interrupt)
    t.daemon = True
    t.start()
    return t


def _latest_period(con: duckdb.DuckDBPyConnection) -> str | None:
    """Latest published reporting period from `meta`, or None if unavailable."""
    try:
        row = con.execute("SELECT max(latest_reporting_period) FROM meta").fetchone()
    except duckdb.Error:
        return None
    return row[0] if row else None


def _period_basis_note(query: str, latest: str | None) -> str | None:
    """Warn when a query counts an all-history dimension as if it were one period."""
    scannable = _LITERAL_OR_COMMENT.sub(" ", query).lower()
    hits = [t for t in _ALL_HISTORY_TABLES if re.search(rf"\b{t}\b", scannable)]
    if not hits:
        return None
    period = latest or "the latest period"
    verb, pron = ("is", "it") if len(hits) == 1 else ("are", "them")
    return (f"{', '.join(hits)} {verb} all-history (latest row per line/owner, no "
            f"reporting_period column). If you are COUNTING or aggregating rows from "
            f"{pron}, the figure spans every published period, not one — for a "
            f"single-period inventory aggregate raw_project_detail / schedule_history / "
            f"budget_history filtered by reporting_period (latest = {period}). A pure "
            f"enrichment JOIN is fine.")


def run_sql_on(con: duckdb.DuckDBPyConnection, query: str, *,
               row_cap: int = RUN_SQL_ROW_CAP,
               timeout: int = RUN_SQL_TIMEOUT_SECONDS) -> dict:
    """Execute a validated SELECT inline, capped at `row_cap` rows."""
    q = validate_select(query)
    timer = _interrupt_after(con, timeout)
    try:
        # newline before ')' so a query ending in a `--` comment can't swallow the wrap
        cur = con.execute(f"SELECT * FROM ({q}\n) AS _sub LIMIT {row_cap + 1}")
        cols = [d[0] for d in cur.description]
        fetched = cur.fetchall()
    finally:
        timer.cancel()
    truncated = len(fetched) > row_cap
    rows = [dict(zip(cols, r)) for r in fetched[:row_cap]]
    latest = _latest_period(con)
    result = {
        "rows": rows,
        "truncated": truncated,
        "latest_reporting_period": latest,
        "provenance": provenance_block(
            definition="raw run_sql result",
            scope={"row_cap": row_cap},
            row_count=len(rows),
            reproduce_sql=q,
        ),
    }
    note = _period_basis_note(q, latest)
    if note:
        result["period_basis_note"] = note
    return result
