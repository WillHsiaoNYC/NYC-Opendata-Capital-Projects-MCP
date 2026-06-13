# src/od_cpd/tools/_common.py
from __future__ import annotations

from ..dbio import sql_literal


def interpolate_sql(sql: str, params: list) -> str:
    """Inline params into a '?'-parameterized SQL string for a self-contained reproduce_sql.

    Splits on '?' and interleaves (so a substituted value containing '?' is not
    re-substituted). Strings are single-quote-escaped; None → NULL; numbers as-is.
    Returns the sql unchanged if the placeholder/param counts disagree.
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        return sql
    out = parts[0]
    for v, nxt in zip(params, parts[1:]):
        out += sql_literal(v) + nxt
    return out


def escape_like(s: str) -> str:
    """Escape LIKE wildcards so user text matches literally.

    Build the predicate with LIKE_ESC / ILIKE_ESC below — DuckDB has no default
    escape char, so a pattern escaped here but compared with a bare LIKE silently
    matches nothing ('\\%' would require a literal backslash).
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# The operator half of escape_like(): keeps the pattern and its ESCAPE clause together.
LIKE_ESC = "LIKE ? ESCAPE '\\'"
ILIKE_ESC = "ILIKE ? ESCAPE '\\'"

# One caption for every tool that groups by borough, so the derivation rule reads
# identically everywhere (borough is line-keyed; the PID scalar is derived).
BOROUGH_GROUP_NOTE = (
    "Borough derives from the project's funding lines (location keys to the budget "
    "line): 'Multiple' = lines in 2+ specific boroughs; 'Citywide' = only "
    "citywide-registered lines.")

def category_pid_filter(alias: str = "s") -> str:
    """SQL fragment scoping a PID set to one category via its all-history links.

    Takes one '?' param (the category). Shared by rank_projects and
    project_portfolio so the two filters can't drift.
    """
    return (f"{alias}.pid IN (SELECT l.pid FROM schedule_budget_link l "
            f"JOIN category_dim c USING (fms_id) WHERE c.category = ?)")


# Schedule-side category grouping counts a PID once in EACH of its categories
# (owner ruling 2026-06-12) — totals across categories exceed the PID count.
CATEGORY_GROUP_NOTE = (
    "Category is line-keyed (category_dim); a project whose funding lines span 2+ "
    "categories counts once in EACH — do not sum category figures into a grand "
    "total. PIDs with no linked budget line at the period are absent.")


def current_period(con, table: str = "schedule_history") -> str:
    """Latest reporting period present in a NORMALIZED table (already off-cadence-filtered)."""
    return con.execute(f"SELECT max(reporting_period) FROM {table}").fetchone()[0]


def direction_of(value, kind: str = "schedule"):
    """Map a signed variance to a neutral direction enum (CR1/CR2)."""
    if value is None:
        return None
    v = float(value)
    if kind == "budget":
        return "increased" if v > 0 else "decreased" if v < 0 else "unchanged"
    return "later" if v > 0 else "earlier" if v < 0 else "unchanged"


def signed_metric(value, kind: str = "schedule") -> dict:
    """A signed value carries its direction so the agent narrates neutrally."""
    return {"value": value, "direction": direction_of(value, kind)}


def mm_envelope(*, anchor_type: str, anchor_id: str, linked: list[dict]) -> dict:
    """Build the M:M envelope. anchor_type ∈ {'schedule','budget'}.

    Cardinality-scaled caveat (DR2): count==1 → light 1:1 note; count>1 → full M:M.
    """
    counterpart_key = "linked_budgets" if anchor_type == "schedule" else "linked_schedules"
    other = "budget" if anchor_type == "schedule" else "schedule"
    n = len(linked)
    if n == 0:
        caveat = f"No linked {other} found for this {anchor_type} in the latest period."
    elif n == 1:
        caveat = (f"This {anchor_type} maps 1:1 to its counterpart. "
                  "Most relationships are 1:1.")
    else:
        caveat = (f"This {anchor_type} fans out to {n} counterparts (many-to-many) — "
                  "all are listed; never collapse to one.")
    return {"anchor": {"type": anchor_type, "id": anchor_id},
            counterpart_key: linked, "caveat": caveat}
