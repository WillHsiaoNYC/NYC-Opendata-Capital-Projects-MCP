# src/od_cpd/socrata.py
from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx

from . import schema
from .config import DATASETS, PAGE_SIZE, SOCRATA_DOMAIN, app_token


@dataclass(frozen=True)
class Metadata:
    rows_updated_at: int
    columns: list[str]
    export_headers: dict[str, str] | None = None


def _headers() -> dict[str, str]:
    tok = app_token()
    return {"X-App-Token": tok} if tok else {}


_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _retryable(exc: Exception) -> bool:
    return isinstance(exc, httpx.TransportError) or (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _RETRY_STATUSES
    )


def _get(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    for attempt in range(3):
        try:
            response = client.get(url, **kwargs)
            response.raise_for_status()
            return response
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if attempt == 2 or not _retryable(exc):
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def fetch_metadata(dataset_id: str, *, client: httpx.Client | None = None) -> Metadata:
    url = f"https://{SOCRATA_DOMAIN}/api/views/{dataset_id}.json"
    owns = client is None
    client = client or httpx.Client(timeout=60, headers=_headers())
    try:
        resp = _get(client, url)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns:
            client.close()
    cols = [c.get("fieldName") for c in data.get("columns", []) if c.get("fieldName")]
    export_headers: dict[str, str] = {}
    for column in data.get("columns", []):
        field = column.get("fieldName")
        if not field or field.startswith(":"):
            continue
        label = column.get("name", field)
        if label in export_headers:
            raise ValueError(f"{dataset_id}: duplicate export column label {label!r}")
        export_headers[label] = field
    return Metadata(rows_updated_at=int(data.get("rowsUpdatedAt", 0)), columns=cols,
                    export_headers=export_headers)


def fetch_row_count(dataset_id: str, *, client: httpx.Client | None = None) -> int:
    """Read the source's independent row count for download reconciliation."""
    owns = client is None
    client = client or httpx.Client(timeout=60, headers=_headers())
    try:
        resp = _get(client, f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.json",
                          params={"$select": "count(*) AS row_count"})
        resp.raise_for_status()
        return int(resp.json()[0]["row_count"])
    finally:
        if owns:
            client.close()


def fetch_period_counts(dataset_id: str, *, client: httpx.Client | None = None) -> dict[str, int]:
    """Read snapshot period coverage; adopted-budget months are not snapshots."""
    period = DATASETS[dataset_id].period_column
    params = {"$select": f"{period}, count(*) AS row_count", "$group": period,
              "$order": period, "$limit": 10000}
    if dataset_id == "qj5n-h5qp":
        params["$where"] = "spend_to_date IS NOT NULL"
    owns = client is None
    client = client or httpx.Client(timeout=60, headers=_headers())
    try:
        resp = _get(client, f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.json", params=params)
        resp.raise_for_status()
        return {str(row.get(period, "")): int(row["row_count"]) for row in resp.json()}
    finally:
        if owns:
            client.close()


def download_csv(
    dataset_id: str,
    out_path: Path,
    *,
    page_size: int = PAGE_SIZE,
    client: httpx.Client | None = None,
    expected_header: list[str] | None = None,
) -> int:
    """Stream the full dataset to `out_path` as CSV. Returns data-row count.

    Every parsed page header must match before any of its rows are appended.
    Header is written once (from page 0); subsequent pages drop their header.
    Stops when a page returns fewer than `page_size` data rows.
    """
    if page_size < 1:
        raise ValueError("page_size must be positive")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.csv"
    owns = client is None
    client = client or httpx.Client(timeout=300, headers=_headers())
    total = 0
    offset = 0
    expected = [column.strip().lower() for column in expected_header] if expected_header else None
    try:
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            while True:
                params = {"$limit": page_size, "$offset": offset, "$order": ":id"}
                resp = _get(client, base, params=params)
                resp.raise_for_status()
                text = resp.text
                reader = csv.reader(io.StringIO(text, newline=""), strict=True)
                try:
                    parsed_header = next(reader)
                except StopIteration:
                    raise ValueError(f"{dataset_id}: missing CSV header at offset {offset}") from None
                header_lines = reader.line_num
                normalized = [column.lstrip("\ufeff").strip().lower() for column in parsed_header]
                if expected is None:
                    expected = normalized
                if normalized != expected:
                    raise ValueError(
                        f"{dataset_id}: CSV header changed at offset {offset}: "
                        f"got {normalized}, expected {expected}"
                    )
                n = 0
                for row in reader:
                    if len(row) != len(expected):
                        raise ValueError(f"{dataset_id}: malformed CSV row at offset {offset + n}")
                    n += 1
                if n > page_size:
                    raise ValueError(f"{dataset_id}: source exceeded requested page size")
                lines = text.splitlines(keepends=True)
                if offset == 0:
                    fh.writelines(lines[:header_lines])
                fh.writelines(lines[header_lines:])
                if n and not text.endswith(("\n", "\r")):
                    fh.write("\n")  # A valid page may omit its final record terminator.
                total += n
                offset += page_size
                if n < page_size:
                    break
    finally:
        if owns:
            client.close()
    return total


def normalize_export_csv(
    dataset_id: str,
    original_path: Path,
    out_path: Path,
    *,
    metadata: Metadata,
) -> int:
    """Map source display headings to verified raw fields, preserving cell values."""
    expected = schema.RAW_COLUMNS[schema.TABLE_FOR_DATASET[dataset_id]]
    mapping = metadata.export_headers or {}
    with original_path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        try:
            header = next(reader)
        except StopIteration:
            raise ValueError(f"{dataset_id}: missing export CSV header") from None
        mapped = []
        for label in header:
            candidates = {mapping[label]} if label in mapping else set()
            if label in expected:
                candidates.add(label)
            if len(candidates) != 1:
                raise ValueError(f"{dataset_id}: unknown or ambiguous export header {label!r}")
            mapped.append(candidates.pop())
        if len(set(mapped)) != len(mapped) or set(mapped) != set(expected):
            raise ValueError(f"{dataset_id}: export CSV header does not match expected fields")
        positions = [mapped.index(field) for field in expected]
        out_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with out_path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(expected)
            for row in reader:
                if len(row) != len(mapped):
                    raise ValueError(f"{dataset_id}: malformed export CSV row {count + 1}")
                writer.writerow([row[position] for position in positions])
                count += 1
    return count



def export_url(dataset_id: str) -> str:
    """Export every raw field as text to preserve source precision and timestamps.

    Plain SODA3 exports apply display formatting, including rounded percentages
    and currencies. Server-side text casts retain the machine-readable values.
    No LIMIT or pagination is applied: each response contains the full dataset.
    """
    columns = schema.RAW_COLUMNS[schema.TABLE_FOR_DATASET[dataset_id]]
    query = "SELECT " + ", ".join(f"{column}::text AS {column}" for column in columns)
    return f"https://{SOCRATA_DOMAIN}/api/v3/views/{dataset_id}/export.csv?{urlencode({'query': query})}"


def download_export_csv(
    dataset_id: str,
    out_path: Path,
    *,
    metadata: Metadata,
    client: httpx.Client | None = None,
) -> int:
    """Download one full CSV export, retaining its original bytes beside normalized CSV.

    Each transient failure restarts the entire transfer, up to three attempts.
    The retained source file is ``out_path.with_suffix('.original.csv')``.
    """
    original_path = out_path.with_suffix(".original.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    url = export_url(dataset_id)
    owns = client is None
    client = client or httpx.Client(timeout=300, headers=_headers(), follow_redirects=True)
    try:
        for attempt in range(3):
            try:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    with original_path.open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
                break
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == 2 or not _retryable(exc):
                    raise
                time.sleep(2 ** attempt)
    finally:
        if owns:
            client.close()
    return normalize_export_csv(dataset_id, original_path, out_path, metadata=metadata)
