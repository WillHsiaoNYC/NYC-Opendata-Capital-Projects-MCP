"""Verified full CSV snapshots attached to this project's GitHub releases."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from . import schema, socrata
from .config import DATASETS

REPOSITORY = "WillHsiaoNYC/NYC-Opendata-Capital-Projects-MCP"
MANIFEST_FILENAME = "manifest.json"
MANIFEST_VERSION = 1


def _tag(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise ValueError("Snapshot release tag must contain only letters, digits, '.', '_' or '-'")
    return value


def _release_asset_url(asset_id: int) -> str:
    if not _positive(asset_id):
        raise ValueError("Snapshot release requires a positive GitHub asset ID")
    return f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset_id}"


def _positive(value) -> bool:
    return type(value) is int and value > 0


def validate_manifest(manifest: dict) -> None:
    """Reject incomplete or incompatible manifests before fetching CSV assets."""
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != MANIFEST_VERSION:
        raise ValueError("Unsupported snapshot manifest schema_version")
    try:
        stamp = datetime.fromisoformat(manifest["downloaded_at"].replace("Z", "+00:00"))
        if stamp.utcoffset() is None:
            raise ValueError()
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("Snapshot manifest requires a timezone-aware downloaded_at timestamp") from None
    if manifest.get("export_format") != "socrata-text-v1":
        raise ValueError("Snapshot requires lossless socrata-text-v1 export_format")
    entries = manifest.get("datasets")
    if not isinstance(entries, dict) or set(entries) != set(DATASETS):
        raise ValueError("Snapshot manifest must contain exactly all four datasets")
    for ds, entry in entries.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{ds}: invalid snapshot entry")
        expected = schema.RAW_COLUMNS[schema.TABLE_FOR_DATASET[ds]]
        mapping = entry.get("export_headers")
        if entry.get("filename") != f"{ds}.csv":
            raise ValueError(f"{ds}: invalid snapshot filename")
        if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError(f"{ds}: invalid snapshot SHA-256")
        if not all(_positive(entry.get(key)) for key in ("size_bytes", "row_count", "rows_updated_at")):
            raise ValueError(f"{ds}: snapshot sizes, counts and revision must be positive integers")
        columns = entry.get("columns")
        if (not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)
                or len(set(columns)) != len(columns)
                or set(c for c in columns if not c.startswith(":")) != set(expected)):
            raise ValueError(f"{ds}: snapshot columns do not match the supported schema")
        if (not isinstance(mapping, dict) or len(mapping) != len(expected)
                or not all(isinstance(key, str) and key for key in mapping)
                or not all(isinstance(v, str) for v in mapping.values())
                or sorted(mapping.values()) != sorted(expected)):
            raise ValueError(f"{ds}: invalid export header mapping")
        if entry.get("source_url") != socrata.export_url(ds):
            raise ValueError(f"{ds}: invalid snapshot source URL")
        counts = entry.get("period_counts")
        if (not isinstance(counts, dict) or not counts
                or any(not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{3}(01|05|09)", key) or not _positive(value)
                       for key, value in counts.items())
                or sum(counts.values()) > entry["row_count"]):
            raise ValueError(f"{ds}: invalid snapshot reporting period counts")


def resolve_release(tag: str = "data-latest", *, client: httpx.Client | None = None) -> dict:
    """Resolve a complete release; never accept download URLs supplied by a manifest."""
    tag = _tag(tag)
    owns = client is None
    client = client or httpx.Client(timeout=60, follow_redirects=True)
    try:
        try:
            response = socrata._get(client, f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{quote(tag, safe='')}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ValueError(
                    f"GitHub snapshot release '{tag}' is unavailable. No snapshot may have been "
                    "published yet; use --source opendata or select an existing --release."
                ) from exc
            raise
        release = response.json()
        if release.get("draft") or release.get("tag_name") != tag:
            raise ValueError("Snapshot release is unpublished or has an unexpected tag")
        names = [asset.get("name") for asset in release.get("assets", [])]
        required = [MANIFEST_FILENAME, *(f"{ds}.csv" for ds in DATASETS)]
        if any(names.count(name) != 1 for name in required):
            raise ValueError("Snapshot release is missing required assets or contains duplicates")
        asset_ids = {asset["name"]: asset.get("id") for asset in release["assets"] if asset.get("name") in required}
        if any(not _positive(value) for value in asset_ids.values()):
            raise ValueError("Snapshot release assets have invalid IDs")
        response = socrata._get(client, _release_asset_url(asset_ids[MANIFEST_FILENAME]),
                               headers={"Accept": "application/octet-stream"})
        manifest = response.json()
        validate_manifest(manifest)
        return {**manifest, "release_tag": tag, "release_assets": asset_ids}
    finally:
        if owns:
            client.close()


def period_counts(path: Path, dataset_id: str) -> dict[str, int]:
    """Match ingestion coverage: history adoption rows are not snapshots."""
    counts: Counter = Counter()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream, strict=True):
            period = row[DATASETS[dataset_id].period_column]
            if period and (dataset_id != "qj5n-h5qp" or row["spend_to_date"] != ""):
                counts[period] += 1
    return dict(sorted(counts.items()))


def download_dataset(dataset_id: str, directory: Path, manifest: dict, *,
                     client: httpx.Client | None = None) -> tuple[socrata.Metadata, Path, int]:
    """Verify original bytes and normalized coverage without contacting Open Data."""
    validate_manifest(manifest)
    entry = manifest["datasets"][dataset_id]
    _tag(manifest.get("release_tag"))
    asset_url = _release_asset_url(manifest.get("release_assets", {}).get(entry["filename"]))
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / entry["filename"]
    normalized = directory / f"{dataset_id}.normalized.csv"
    part = original.with_suffix(".csv.part")
    owns = client is None
    client = client or httpx.Client(timeout=300, follow_redirects=True)
    try:
        for attempt in range(3):
            try:
                digest = hashlib.sha256()
                size = 0
                with client.stream("GET", asset_url, headers={"Accept": "application/octet-stream"}) as response:
                    response.raise_for_status()
                    with part.open("wb") as target:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > entry["size_bytes"]:
                                raise ValueError(f"{dataset_id}: snapshot byte size exceeds manifest")
                            digest.update(chunk)
                            target.write(chunk)
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == 2 or not socrata._retryable(exc):
                    raise
                time.sleep(2 ** attempt)
        if size != entry["size_bytes"] or digest.hexdigest() != entry["sha256"]:
            raise ValueError(f"{dataset_id}: snapshot byte size or SHA-256 mismatch")
        meta = socrata.Metadata(entry["rows_updated_at"], entry["columns"], entry["export_headers"])
        count = socrata.normalize_export_csv(dataset_id, part, normalized, metadata=meta)
        if count != entry["row_count"] or period_counts(normalized, dataset_id) != entry["period_counts"]:
            raise ValueError(f"{dataset_id}: snapshot row count or reporting period coverage mismatch")
        part.replace(original)
        return meta, normalized, count
    finally:
        part.unlink(missing_ok=True)
        if owns:
            client.close()


def write_manifest(output_dir: Path, downloads: dict) -> Path:
    """Describe validated normalized downloads and their unchanged original exports."""
    if set(downloads) != set(DATASETS):
        raise ValueError("Snapshot creation requires all four validated datasets")
    path = output_dir / MANIFEST_FILENAME
    if path.exists() or any((output_dir / f"{ds}.csv").exists() for ds in DATASETS):
        raise ValueError("Snapshot output already contains release artifacts; choose an empty output directory")
    entries = {}
    for ds, (meta, normalized, count) in downloads.items():
        original = output_dir / f"{ds}.csv"
        source = normalized.with_suffix(".original.csv")
        with normalized.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            expected = schema.RAW_COLUMNS[schema.TABLE_FOR_DATASET[ds]]
            if next(reader, None) != expected:
                raise ValueError(f"{ds}: normalized snapshot header does not match supported schema")
            actual_count = 0
            for row in reader:
                if len(row) != len(expected):
                    raise ValueError(f"{ds}: malformed normalized snapshot row")
                actual_count += 1
        with source.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            header = next(reader, [])
            original_count = 0
            for row in reader:
                if len(row) != len(header):
                    raise ValueError(f"{ds}: malformed original snapshot row")
                original_count += 1
        if not _positive(count) or actual_count != count or original_count != count:
            raise ValueError(f"{ds}: snapshot row count does not match validated downloads")
        if source.resolve() != original.resolve():
            shutil.copyfile(source, original)
        digest = hashlib.sha256()
        with original.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        entries[ds] = {"filename": original.name, "sha256": digest.hexdigest(),
                       "size_bytes": original.stat().st_size, "row_count": count,
                       "rows_updated_at": meta.rows_updated_at, "columns": meta.columns,
                       "export_headers": meta.export_headers,
                       "source_url": socrata.export_url(ds),
                       "period_counts": period_counts(normalized, ds)}
    manifest = {"schema_version": MANIFEST_VERSION, "export_format": "socrata-text-v1",
                "downloaded_at": datetime.now(timezone.utc).isoformat(), "datasets": entries}
    validate_manifest(manifest)
    staged = path.with_suffix(".json.part")
    staged.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)
    return path
