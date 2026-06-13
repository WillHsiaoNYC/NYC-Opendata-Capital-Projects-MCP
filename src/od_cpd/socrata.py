# src/od_cpd/socrata.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import PAGE_SIZE, SOCRATA_DOMAIN, app_token


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


def download_csv(
    dataset_id: str,
    out_path: Path,
    *,
    page_size: int = PAGE_SIZE,
    client: httpx.Client | None = None,
) -> int:
    """Stream the full dataset to `out_path` as CSV. Returns data-row count.

    Header is written once (from page 0); subsequent pages drop their header.
    Stops when a page returns fewer than `page_size` data rows.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base = f"https://{SOCRATA_DOMAIN}/resource/{dataset_id}.csv"
    owns = client is None
    client = client or httpx.Client(timeout=300, headers=_headers())
    total = 0
    offset = 0
    try:
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            while True:
                params = {"$limit": page_size, "$offset": offset, "$order": ":id"}
                resp = client.get(base, params=params)
                resp.raise_for_status()
                lines = resp.text.splitlines(keepends=True)
                if not lines:
                    break
                header, body = lines[0], lines[1:]
                if offset == 0:
                    fh.write(header)
                fh.writelines(body)
                n = len(body)
                total += n
                offset += page_size
                if n < page_size:
                    break
    finally:
        if owns:
            client.close()
    return total
