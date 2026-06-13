# src/od_cpd/agencies.py
from __future__ import annotations

from pathlib import Path

import yaml

from .config import data_dir

# The 13 agencies that ever manage a PID (spec §6.4 invariant).
SCHEDULE_EXECUTORS: set[str] = {
    "DDC", "EDC", "DEP", "DOT", "DPR", "DOC", "CUNY",
    "NYPD", "FDNY", "DCAS", "DHS", "DOHMH", "DSNY",
}


def load_agency_rows(*, yaml_path: Path | None = None) -> list[dict]:
    """Parse agencies.yaml into agency_dim rows.

    cpd_active / row_count_live are set later by ingest (None/0 here).
    """
    yaml_path = yaml_path or (data_dir() / "agencies.yaml")
    raw = yaml.safe_load(yaml_path.read_text()) or {}
    rows: list[dict] = []
    for slug, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        acronym = entry.get("cpdw_acronym")
        rows.append({
            "slug": slug,
            "display_name": entry.get("display", slug),
            "aliases": list(entry.get("aliases", []) or []),
            "cpdw_acronym": acronym,
            "cpd_active": None,
            "is_schedule_executor": bool(acronym in SCHEDULE_EXECUTORS),
            "row_count_live": 0,
            "role_default": entry.get("role_default", "sponsor"),
        })
    return rows
