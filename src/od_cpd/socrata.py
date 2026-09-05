# src/od_cpd/socrata.py
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import DATASETS, PAGE_SIZE, SOCRATA_DOMAIN, app_token


@dataclass(frozen=True)
class Metadata:
    rows_updated_at: int
    columns: list[str]


def _headers() -> dict[str, str]:
    tok = app_token()
    return {"X-App-Token": tok} if tok else {}


def fetch_metadata(dataset_id: str, *, client: httpx.Client | None = None) -> Metadata:
    url = f"https://{SOCRATA_DOMAIN}/api/views/{dataset_id}.json"
    owns = client is None
    client = client or httpx.Client(timeout=60, headers=_headers())
    try:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns:
            client.close()
    cols = [c.get("fieldName") for c in data.get("columns", []) if c.get("fieldName")]
    return Metadata(rows_updated_at=int(data.get("rowsUpdatedAt", 0)), columns=cols)


def fetch_row_count(dataset_id: str, *, client: httpx.Client | None = None) -> int:
    """Read the source's independent row count for download reconciliation."""
    owns = client is None
    client = client or httpx.Client(timeout=60, headers=_headers())
    try:
        resp = client.get(f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.json",
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
        resp = client.get(f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.json", params=params)
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
                resp = client.get(base, params=params)
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
