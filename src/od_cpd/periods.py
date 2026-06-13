# src/od_cpd/periods.py
from __future__ import annotations

import statistics

from .config import CADENCE_MONTHS, FULL_PERIOD_MIN_FRACTION


def is_cadence_period(period: str) -> bool:
    """True if `period` is a 6-digit YYYYMM ending in a cadence month."""
    if not period or len(period) != 6 or not period.isdigit():
        return False
    return period[4:6] in CADENCE_MONTHS


def resolve_current_period(period_counts: dict[str, int]) -> str | None:
    """Latest cadence period whose row count is a 'full' period.

    `period_counts` maps period string -> row count for that period.
    Returns None if there is no qualifying period.
    """
    cadence = {p: c for p, c in period_counts.items() if is_cadence_period(p)}
    if not cadence:
        return None
    median = statistics.median(cadence.values())
    threshold = median * FULL_PERIOD_MIN_FRACTION
    full = [p for p, c in cadence.items() if c >= threshold]
    if not full:
        return None
    return max(full)  # periods are zero-padded YYYYMM → lexical max == latest
