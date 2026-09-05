# src/od_cpd/categories.py
"""Program/facility category taxonomy: load categories.yaml, compile it to a
DuckDB CASE expression, and materialize one category per (managing_agency, fms_id).

Classification is a 3-tier precedence (see data/categories.yaml header):
  1. specific ten-year keyword / fms-id prefix / ever-managed-by (file order)
  2. sponsor_agency routing
  3. generic facility keywords
  4. other_label

"Owner-authoritative" categories (Library, Cultural) declare `ever_managed_by`:
any fms_id ever managed OR sponsored by those agencies (across all history) pins
the category at tier 1, beating work-type keywords. Each such category gets its
OWN all-history flag so multiple declarers never collide.

The taxonomy is a trusted local file, so rule strings are interpolated into SQL
(single-quote-escaped via dbio.sql_literal). No user input reaches the SQL.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

from .config import data_dir
from .dbio import sql_literal


def load_category_rules(*, yaml_path: Path | None = None) -> dict:
    """Parse categories.yaml → {'categories': [...], 'other_label': str}."""
    yaml_path = yaml_path or (data_dir() / "categories.yaml")
    raw = yaml.safe_load(yaml_path.read_text()) or {}
    return {
        "categories": list(raw.get("categories") or []),
        "other_label": raw.get("other_label", "Other / Uncategorized"),
    }


def category_names(rules: dict) -> list[str]:
    """Canonical ordered category list, including the catch-all."""
    return [c["name"] for c in rules["categories"]] + [rules["other_label"]]


def _keyword_conds(cat: dict, *, tycs: str, fms: str, ever_flags: dict[str, str]) -> list[str]:
    conds: list[str] = []
    if cat.get("ever_managed_by"):
        conds.append(ever_flags[cat["name"]])
    for p in cat.get("fms_prefix") or []:
        conds.append(f"{fms} ILIKE {sql_literal(p + '%')}")
    for kw in cat.get("ten_year_any") or []:
        conds.append(f"len([label FOR label IN {tycs} "
                     f"IF label ILIKE {sql_literal('%' + kw + '%')}]) > 0")
    return conds


def build_category_expr(rules: dict, *, tycs: str, fms: str, sponsors: str,
                        ever_flags: dict[str, str]) -> str:
    """Compile the taxonomy to a SQL expression yielding the category for one row.
    Precedence (first non-null): specific keyword/prefix → sponsor → generic keyword → other."""
    cats = rules["categories"]
    specific_whens, generic_whens = [], []
    for c in cats:
        conds = _keyword_conds(c, tycs=tycs, fms=fms, ever_flags=ever_flags)
        if not conds:
            continue
        when = f"WHEN ({' OR '.join(conds)}) THEN {sql_literal(c['name'])}"
        (generic_whens if c.get("generic") else specific_whens).append(when)
    sponsor_whens = [f"WHEN list_contains({sponsors}, {sql_literal(a)}) "
                     f"THEN {sql_literal(c['name'])}"
                     for c in cats for a in (c.get("sponsor_agencies") or [])]

    parts = [f"CASE {' '.join(whens)} END"
             for whens in (specific_whens, sponsor_whens, generic_whens) if whens]
    parts.append(sql_literal(rules["other_label"]))
    return f"COALESCE({', '.join(parts)})"


def build_category_dim(con: duckdb.DuckDBPyConnection, *, rules: dict | None = None) -> None:
    """Build category_dim at the (managing_agency, fms_id) budget-line grain.

    Keep all values tied at each attribute's latest nonempty period for that line.
    Match any tied label/atomic sponsor, then use taxonomy tier and file order to
    resolve conflicts. Other holders' labels and ordinary sponsors never cross
    the line key. Only declared institution-owner history carries across holders
    of an FMS ID, preserving the existing reassignment rule.

    Reads only RAW tables and runs after raw load is complete.
    """
    rules = rules or load_category_rules()
    ever_cats = [c for c in rules["categories"] if c.get("ever_managed_by")]
    flag_cols, ever_flags = [], {}
    for i, c in enumerate(ever_cats):
        agls = ", ".join(sql_literal(a) for a in c["ever_managed_by"])
        flag_cols.append(f"bool_or(agency IN ({agls})) AS ever_{i}")
        ever_flags[c["name"]] = f"COALESCE(ef.ever_{i}, FALSE)"
    ever_select = ", ".join(flag_cols) if flag_cols else "FALSE AS ever_none"

    # Without a known owner, use this line's holder as the sponsor-tier fallback.
    expr = build_category_expr(
        rules, tycs="t.tycs", fms="d.fms_id",
        sponsors="COALESCE(s.sponsors, [d.managing_agency])", ever_flags=ever_flags)
    con.execute(f"""
        CREATE OR REPLACE TABLE category_dim AS
        WITH detail AS (
            SELECT fms_id, managing_agency, reporting_period,
                   nullif(trim(ten_year_plan_category), '') AS tyc,
                   sponsor_agency
            FROM raw_project_detail
            WHERE fms_id IS NOT NULL
        ),
        sponsor_atoms AS (
            SELECT fms_id, managing_agency, reporting_period, upper(trim(atom)) AS sponsor
            FROM detail CROSS JOIN unnest(string_split(sponsor_agency, ',')) AS u(atom)
            WHERE trim(atom) <> ''
        ),
        latest_labels AS (
            SELECT fms_id, managing_agency, list(DISTINCT tyc ORDER BY tyc) AS tycs
            FROM (
                SELECT * FROM detail WHERE tyc IS NOT NULL
                QUALIFY reporting_period = max(reporting_period) OVER
                    (PARTITION BY fms_id, managing_agency)
            ) GROUP BY fms_id, managing_agency
        ),
        latest_sponsors AS (
            SELECT fms_id, managing_agency,
                   list(DISTINCT sponsor ORDER BY sponsor) AS sponsors
            FROM (
                SELECT * FROM sponsor_atoms
                QUALIFY reporting_period = max(reporting_period) OVER
                    (PARTITION BY fms_id, managing_agency)
            ) GROUP BY fms_id, managing_agency
        ),
        ever_flags AS (
            SELECT fms_id, {ever_select}
            FROM (
                SELECT fms_id, managing_agency AS agency FROM detail
                UNION ALL
                SELECT fms_id, sponsor AS agency FROM sponsor_atoms
                UNION ALL
                SELECT fms_id, managing_agency AS agency FROM raw_budget_history WHERE fms_id IS NOT NULL
            ) GROUP BY fms_id
        ),
        ids AS (
            SELECT fms_id, managing_agency FROM detail WHERE managing_agency IS NOT NULL
            UNION
            SELECT fms_id, managing_agency FROM raw_budget_history
            WHERE fms_id IS NOT NULL AND managing_agency IS NOT NULL
        )
        SELECT d.fms_id, d.managing_agency, {expr} AS category
        FROM ids d
        LEFT JOIN latest_labels t USING (fms_id, managing_agency)
        LEFT JOIN latest_sponsors s USING (fms_id, managing_agency)
        LEFT JOIN ever_flags ef USING (fms_id)
    """)
