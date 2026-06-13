# src/od_cpd/cli.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import typer

from .config import DATASETS, db_path
from .dbio import connect_readonly, DBMissingError
from .ingest import run_ingest
from .schema import SCHEMA_VERSION
from .socrata import fetch_metadata

app = typer.Typer(help="OD_CPD — NYC Capital Projects data manager")


@app.command()
def init() -> None:
    """Download + materialize all four datasets (full)."""
    summary = run_ingest()
    for ds, n in summary.items():
        typer.echo(f"  {ds}: {n} rows")
    typer.echo(f"Done → {db_path()}")


@app.command()
def update() -> None:
    """Re-ingest if any dataset is newer on Socrata than the local copy."""
    try:
        con = connect_readonly()
        local = dict(con.execute("SELECT dataset_id, rows_updated_at FROM meta").fetchall())
        schema_v = con.execute("SELECT min(schema_version) FROM meta").fetchone()[0]
        con.close()
    except DBMissingError:
        local, schema_v = {}, 0
    # A schema bump (new column/table) requires a rebuild even if Socrata data is unchanged.
    if schema_v is not None and schema_v < SCHEMA_VERSION:
        typer.echo(f"Schema v{schema_v} < v{SCHEMA_VERSION} — re-ingesting to migrate.")
        run_ingest()
        typer.echo("Done.")
        raise typer.Exit(0)
    # The four metadata fetches are independent network calls — run them in parallel.
    with ThreadPoolExecutor(max_workers=len(DATASETS)) as ex:
        live = dict(zip(DATASETS, ex.map(
            lambda ds: fetch_metadata(ds).rows_updated_at, DATASETS)))
    stale = [ds for ds in DATASETS if live[ds] > int(local.get(ds, 0))]
    if not stale:
        typer.echo("Up to date.")
        raise typer.Exit(0)
    typer.echo(f"Stale: {', '.join(stale)} — re-ingesting all.")
    run_ingest()
    typer.echo("Done.")


@app.command()
def status() -> None:
    """Per-dataset freshness vs Socrata."""
    try:
        con = connect_readonly()
    except DBMissingError:
        typer.echo("DB not initialized — run `od-cpd init`.")
        raise typer.Exit(0)
    rows = con.execute(
        "SELECT dataset_id, row_count, latest_reporting_period, "
        "rows_updated_at, ingest_completed_at FROM meta ORDER BY dataset_id"
    ).fetchall()
    con.close()
    for ds, rc, latest, rua, done in rows:
        typer.echo(f"  {ds}: {rc} rows · latest={latest} · ingested {done}")
