"""Identity for a completed materialization and its public source revisions."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from .config import data_dir
from .schema import SCHEMA_VERSION


def current_fingerprint() -> str:
    """Hash the materialization implementation and curated resource contents."""
    root = Path(__file__).parent
    files = {name: root / name for name in (
        "materialize.py", "categories.py", "agencies.py", "data_dictionary.py",
        "periods.py", "schema.py", "config.py", "build_info.py",
    )}
    files.update({f"data/{path.name}": path for path in data_dir().iterdir()
                  if Path(path.name).suffix in {".yaml", ".yml", ".tsv"}})
    digest = hashlib.sha256()
    for name, path in sorted(files.items()):
        digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def write_build_info(con: duckdb.DuckDBPyConnection, *, expected_fingerprint: str) -> dict:
    """Stamp only after the caller has completed every materialization step.

    Source metadata may be absent in synthetic in-memory fixtures; real ingest and
    rematerialize callers enforce complete source readiness before building.
    The caller captures expected_fingerprint before building; a change to rules
    during the build invalidates its result before any completed marker is written.
    """
    try:
        rows = con.execute(
            "SELECT dataset_id, rows_updated_at, row_count, column_hash, "
            "latest_reporting_period FROM meta ORDER BY dataset_id"
        ).fetchall()
    except duckdb.CatalogException:
        rows = []
    sources = {ds: {"rows_updated_at": revision, "row_count": count,
                    "column_hash": columns, "latest_reporting_period": period}
               for ds, revision, count, columns, period in rows}
    source_json = json.dumps(sources, sort_keys=True, separators=(",", ":"))
    fingerprint = current_fingerprint()
    if fingerprint != expected_fingerprint:
        raise ValueError("Materialization rules changed during the build; rebuild from stable inputs")
    build_id = hashlib.sha256(
        f"{SCHEMA_VERSION}\n{fingerprint}\n{source_json}".encode()
    ).hexdigest()
    con.execute("""
        CREATE TABLE IF NOT EXISTS data_build (
            singleton BOOLEAN PRIMARY KEY CHECK (singleton),
            build_id VARCHAR NOT NULL,
            materializer_fingerprint VARCHAR NOT NULL,
            schema_version INTEGER NOT NULL,
            source_revisions JSON NOT NULL,
            built_at TIMESTAMP NOT NULL
        )
    """)
    con.execute(
        "INSERT OR REPLACE INTO data_build VALUES (TRUE, ?, ?, ?, ?, ?)",
        [build_id, fingerprint, SCHEMA_VERSION, source_json, datetime.now(timezone.utc)],
    )
    return read_build_info(con)


def read_build_info(con: duckdb.DuckDBPyConnection) -> dict:
    """Return the completed build identity, or {} for an unstamped database."""
    try:
        row = con.execute(
            "SELECT build_id, materializer_fingerprint, schema_version, "
            "source_revisions, built_at FROM data_build WHERE singleton"
        ).fetchone()
    except duckdb.CatalogException:
        return {}
    if row is None:
        return {}
    return {"build_id": row[0], "materializer_fingerprint": row[1],
            "schema_version": row[2], "source_revisions": json.loads(row[3]),
            "built_at": row[4].isoformat()}
