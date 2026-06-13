# src/od_cpd/categories.py
"""Program/facility category taxonomy: load categories.yaml, compile it to a
DuckDB CASE expression, and materialize a category_dim(fms_id, category) table.

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


def _keyword_conds(cat: dict, *, tyc: str, fms: str, ever_flags: dict[str, str]) -> list[str]:
    conds: list[str] = []
    if cat.get("ever_managed_by"):
        conds.append(ever_flags[cat["name"]])
    for p in cat.get("fms_prefix") or []:
        conds.append(f"{fms} ILIKE {sql_literal(p + '%')}")
    for kw in cat.get("ten_year_any") or []:
        conds.append(f"{tyc} ILIKE {sql_literal('%' + kw + '%')}")
    return conds


def build_category_expr(rules: dict, *, tyc: str, fms: str, sponsor: str,
                        ever_flags: dict[str, str]) -> str:
    """Compile the taxonomy to a SQL expression yielding the category for one row.
    Precedence (first non-null): specific keyword/prefix → sponsor → generic keyword → other."""
    cats = rules["categories"]
    specific_whens, generic_whens = [], []
    for c in cats:
        conds = _keyword_conds(c, tyc=tyc, fms=fms, ever_flags=ever_flags)
        if not conds:
            continue
        when = f"WHEN ({' OR '.join(conds)}) THEN {sql_literal(c['name'])}"
        (generic_whens if c.get("generic") else specific_whens).append(when)
    sponsor_whens = [f"WHEN {sponsor} = {sql_literal(a)} THEN {sql_literal(c['name'])}"
                     for c in cats for a in (c.get("sponsor_agencies") or [])]

    parts = [f"CASE {' '.join(whens)} END"
             for whens in (specific_whens, sponsor_whens, generic_whens) if whens]
    parts.append(sql_literal(rules["other_label"]))
    return f"COALESCE({', '.join(parts)})"


def build_category_dim(con: duckdb.DuckDBPyConnection, *, rules: dict | None = None) -> None:
    """CREATE OR REPLACE category_dim(fms_id, category) at the fms_id grain.

    Reads only RAW tables, so it runs any time after raw load. ten_year_plan_category
    and sponsor are taken from each fms_id's latest project_detail row. Each
    owner-authoritative category gets its own all-history membership flag (ever_N) so
    a reassigned line keeps its category and two declarers (Library, Cultural) don't collide.
    """
    rules = rules or load_category_rules()
    ever_cats = [c for c in rules["categories"] if c.get("ever_managed_by")]
    flag_cols, ever_flags = [], {}
    for i, c in enumerate(ever_cats):
        agls = ", ".join(sql_literal(a) for a in c["ever_managed_by"])
        flag_cols.append(f"bool_or(agency IN ({agls})) AS ever_{i}")
        ever_flags[c["name"]] = f"COALESCE(ef.ever_{i}, FALSE)"
    ever_select = ", ".join(flag_cols) if flag_cols else "FALSE AS ever_none"

    # Detail sponsor where known, else the budget-holder (for budget-only lines with
    # no project_detail row) — last resort, reached only by the sponsor tier.
    expr = build_category_expr(
        rules, tyc="m.tyc", fms="d.fms_id",
        sponsor="COALESCE(m.sponsor, bm.bud_managing)", ever_flags=ever_flags)
    con.execute(f"""
        CREATE OR REPLACE TABLE category_dim AS
        WITH meta AS (
            SELECT fms_id,
                   arg_max(ten_year_plan_category, reporting_period) AS tyc,
                   arg_max(sponsor_agency, reporting_period)         AS sponsor
            FROM raw_project_detail
            WHERE fms_id IS NOT NULL
              AND (ten_year_plan_category IS NOT NULL OR sponsor_agency IS NOT NULL)
            GROUP BY fms_id
        ),
        bud_mgr AS (
            SELECT fms_id, arg_max(managing_agency, year_month_reported) AS bud_managing
            FROM raw_budget_history WHERE fms_id IS NOT NULL GROUP BY fms_id
        ),
        ever_flags AS (
            SELECT fms_id, {ever_select}
            FROM (
                SELECT fms_id, managing_agency AS agency FROM raw_project_detail WHERE fms_id IS NOT NULL
                UNION ALL
                SELECT fms_id, sponsor_agency  AS agency FROM raw_project_detail WHERE fms_id IS NOT NULL
                UNION ALL
                SELECT fms_id, managing_agency AS agency FROM raw_budget_history WHERE fms_id IS NOT NULL
            ) GROUP BY fms_id
        ),
        ids AS (
            SELECT DISTINCT fms_id FROM raw_project_detail WHERE fms_id IS NOT NULL
            UNION
            SELECT DISTINCT fms_id FROM raw_budget_history WHERE fms_id IS NOT NULL
        )
        SELECT d.fms_id, {expr} AS category
        FROM ids d
        LEFT JOIN meta m        ON d.fms_id = m.fms_id
        LEFT JOIN bud_mgr bm    ON d.fms_id = bm.fms_id
        LEFT JOIN ever_flags ef ON d.fms_id = ef.fms_id
    """)
