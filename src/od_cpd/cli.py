# src/od_cpd/cli.py
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from pathlib import Path

import httpx
import typer

from .config import DATASETS, db_path
from .dbio import connect_readonly, DBMissingError
from . import build_info, socrata
from .ingest import run_ingest, run_rematerialize, create_snapshot
from .periods import is_cadence_period, resolve_current_period
from .schema import SCHEMA_VERSION
from .socrata import fetch_metadata

app = typer.Typer(help="OD_CPD — NYC Capital Projects data manager")


class Source(str, Enum):
    opendata = "opendata"
    github = "github"


def _refresh(source: Source, release: str) -> dict:
    try:
        if source == Source.github:
            return run_ingest(source="github", release=release, on_snapshot=_show_snapshot)
        return run_ingest()
    except (httpx.HTTPError, ValueError) as exc:
        typer.echo(f"Refresh failed: {exc}", err=True)
        if source == Source.opendata:
            typer.echo("To explicitly choose a dated GitHub snapshot, retry with --source github. "
                       "All four datasets will be downloaded and rebuilt.", err=True)
        raise typer.Exit(1) from exc


def _show_snapshot(manifest: dict) -> None:
    typer.echo(f"GitHub snapshot {manifest['release_tag']} · downloaded {manifest['downloaded_at']}")
    for ds, entry in manifest["datasets"].items():
        typer.echo(f"  {ds}: reporting periods {', '.join(sorted(entry['period_counts']))}")
    typer.echo("This is a dated snapshot; current Open Data freshness has not been checked.")


@app.command()
def init(source: Source = typer.Option(Source.opendata, help="Download source: opendata or github."),
         release: str = typer.Option("data-latest", help="GitHub data release tag.")) -> None:
    """Download + materialize all four datasets (full)."""
    summary = _refresh(source, release)
    for ds, n in summary.items():
        typer.echo(f"  {ds}: {n} rows")
    typer.echo(f"Done → {db_path()}")
    typer.echo(f"Health reports → {db_path().parent / 'ingest-runs'}")


@app.command()
def update(source: Source = typer.Option(Source.opendata, help="Download source: opendata or github."),
           release: str = typer.Option("data-latest", help="GitHub data release tag."),
           force: bool = typer.Option(False, "--force", help="Fully download and rebuild even if revisions match.")) -> None:
    """Re-ingest if any dataset is newer on Socrata than the local copy."""
    if source == Source.github or force:
        _refresh(source, release)
        typer.echo("Done — all four datasets rebuilt.")
        return
    try:
        with connect_readonly() as con:
            local = dict(con.execute("SELECT dataset_id, rows_updated_at FROM meta").fetchall())
            schema_v = con.execute("SELECT min(schema_version) FROM meta").fetchone()[0]
            build = build_info.read_build_info(con)
    except DBMissingError:
        local, schema_v, build = {}, 0, {}
    if set(local) != set(DATASETS) or schema_v is None or schema_v < 2 or not all(local.values()):
        typer.echo("Source metadata missing or unsupported — re-ingesting all datasets.")
        _refresh(source, release)
        typer.echo("Done.")
        raise typer.Exit(0)
    # The four metadata fetches are independent network calls — run them in parallel.
    try:
        with ThreadPoolExecutor(max_workers=len(DATASETS)) as ex:
            live = dict(zip(DATASETS, ex.map(
                lambda ds: fetch_metadata(ds).rows_updated_at, DATASETS)))
    except httpx.HTTPError as exc:
        typer.echo(f"Cannot check Open Data revisions: {exc}. "
                   "You can explicitly choose a dated snapshot with --source github.", err=True)
        raise typer.Exit(1) from exc
    if any(live[ds] < int(local[ds]) for ds in DATASETS):
        typer.echo("An upstream revision predates the local revision; refresh was not performed. Check status --check-upstream.")
        raise typer.Exit(1)
    stale = [ds for ds in DATASETS if live[ds] > int(local.get(ds, 0))]
    if not stale:
        if schema_v < SCHEMA_VERSION or build.get("materializer_fingerprint") != build_info.current_fingerprint():
            typer.echo("Sources unchanged; rebuilding materialized rules without downloading.")
            run_rematerialize()
            typer.echo("Done.")
            raise typer.Exit(0)
        typer.echo("Up to date.")
        raise typer.Exit(0)
    typer.echo(f"Stale: {', '.join(stale)} — re-ingesting all.")
    _refresh(source, release)
    typer.echo("Done.")


@app.command()
def snapshot(output_directory: Path) -> None:
    """Prepare validated original CSVs and manifest in a new directory; never publish."""
    path = create_snapshot(output_directory)
    typer.echo(f"Validated snapshot prepared: {path}")
    typer.echo("No GitHub release has been published. Upload only manifest.json and the four dataset CSVs.")


@app.command()
def rematerialize() -> None:
    """Rebuild derived tables from the validated local raw data, without downloads."""
    report = run_rematerialize()
    typer.echo(f"Materialized build {report['after']['build']['build_id']} → {db_path()}")
    for warning in report["after"]["warnings"]:
        typer.echo(f"  Warning: {warning}")


def collect_status(*, check_upstream: bool = False) -> dict:
    """Read local state and optionally verify each independent upstream revision."""
    try:
        with connect_readonly() as con:
            rows = con.execute(
                "SELECT dataset_id, row_count, latest_reporting_period, "
                "rows_updated_at, ingest_completed_at FROM meta ORDER BY dataset_id"
            ).fetchall()
            build = build_info.read_build_info(con)
    except DBMissingError:
        rows, build = [], {}
    local = {ds: {"row_count": rc, "latest_reporting_period": latest,
                  "rows_updated_at": revision, "ingest_completed_at": str(done),
                  "freshness": "local_only"}
             for ds, rc, latest, revision, done in rows}
    result = {"check_upstream": check_upstream, "datasets": local, "build": build, "warnings": []}
    if not rows:
        result["warnings"].append("Database is not initialized")
    elif set(local) != set(DATASETS):
        result["warnings"].append("Local source metadata is incomplete")
    if build and build.get("materializer_fingerprint") != build_info.current_fingerprint():
        result["warnings"].append("Materialization rules changed; run od-cpd rematerialize")
    if check_upstream:
        def check(ds):
            info = dict(local.get(ds, {}))
            try:
                before = socrata.fetch_metadata(ds)
                counts = socrata.fetch_period_counts(ds)
                after = socrata.fetch_metadata(ds)
                if before != after or after.rows_updated_at <= 0:
                    info.update(freshness="check_inconclusive",
                                upstream_error="Source changed during the check or revision is unavailable")
                    return ds, info
                revision = int(info.get("rows_updated_at") or 0)
                info["freshness"] = ("not_ingested" if not revision else
                                     "newer_revision" if after.rows_updated_at > revision else
                                     "up_to_date" if after.rows_updated_at == revision else "local_revision_newer")
                full = resolve_current_period(counts)
                observed = max((p for p in counts if is_cadence_period(p)), default=None)
                info.update(upstream_rows_updated_at=after.rows_updated_at,
                            upstream_period_counts=counts, upstream_latest_reporting_period=full,
                            upstream_latest_observed_period=observed)
                info["period_warning"] = ("Latest upstream snapshot has partial coverage"
                                          if observed and observed != full else
                                          "Upstream has no complete snapshot reporting period" if full is None else None)
            except Exception as exc:
                info.update(freshness="upstream_unreachable", upstream_error=str(exc))
            return ds, info
        with ThreadPoolExecutor(max_workers=len(DATASETS)) as ex:
            result["datasets"] = dict(ex.map(check, DATASETS))
    periods = {ds: info.get("latest_reporting_period") for ds, info in result["datasets"].items()}
    if len({period for period in periods.values() if period}) > 1:
        result["warnings"].append(f"Local source reporting periods differ: {periods}")
    return result


@app.command()
def status(check_upstream: bool = typer.Option(False, "--check-upstream", help="Verify Socrata revisions and period coverage.")) -> None:
    """Show local ingestion/build state; optionally verify upstream freshness."""
    result = collect_status(check_upstream=check_upstream)
    if not result["datasets"]:
        typer.echo("DB not initialized — run `od-cpd init`.")
        raise typer.Exit(0)
    typer.echo("Upstream verification requested." if check_upstream else "Local state only; upstream freshness has not been checked.")
    for ds, info in result["datasets"].items():
        typer.echo(f"  {ds}: {info.get('row_count', '?')} rows · local period={info.get('latest_reporting_period')} "
                   f"· source revision={info.get('rows_updated_at')} · ingested={info.get('ingest_completed_at')}")
        if check_upstream:
            typer.echo(f"    {info['freshness']}: upstream revision={info.get('upstream_rows_updated_at')} "
                       f"· upstream full period={info.get('upstream_latest_reporting_period')} "
                       f"· latest observed={info.get('upstream_latest_observed_period')}")
            if info.get("period_warning") or info.get("upstream_error"):
                typer.echo(f"    {info.get('period_warning') or info.get('upstream_error')}")
    if result["build"]:
        typer.echo(f"  Build: {result['build']['build_id']}")
        download = result["build"].get("download")
        if download:
            typer.echo(f"  Download source: {download['source']} · downloaded={download['downloaded_at']}")
            if download.get("release"):
                typer.echo(f"  GitHub release: {download['release']} · snapshot downloaded={download['snapshot_downloaded_at']}")
    for warning in result["warnings"]:
        typer.echo(f"  Warning: {warning}")
