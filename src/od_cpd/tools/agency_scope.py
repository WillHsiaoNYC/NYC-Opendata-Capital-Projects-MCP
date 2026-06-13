# src/od_cpd/tools/agency_scope.py
from __future__ import annotations

import duckdb

from ..dbio import sql_literal


def resolve_agency(con: duckdb.DuckDBPyConnection, agency: str) -> dict | None:
    """User string -> agency_dim row, matched case-insensitively on slug, cpdw_acronym,
    or any alias. Returns {slug, acronym, variants, role_default} or None if unknown.

    `variants` is the set of label strings to match against the data columns (acronym +
    aliases), so an agency the data records under more than one label (e.g. 'H+H' and
    'HHC') is matched under all of them.
    """
    row = con.execute(
        """
        SELECT slug, cpdw_acronym, aliases, role_default
        FROM agency_dim
        WHERE lower(?) IN (lower(slug), lower(COALESCE(cpdw_acronym, '')))
           OR EXISTS (SELECT 1 FROM unnest(aliases) AS a(x) WHERE lower(x) = lower(?))
        -- deterministic tiebreaker for acronyms shared by >1 entry (e.g. OTI=doitt/oti,
        -- HRO=nycha/hro): prefer a capital-project agency (non-null acronym) over a
        -- dictionary-only one, then by slug.
        ORDER BY (cpdw_acronym IS NULL), slug
        LIMIT 1
        """,
        [agency, agency],
    ).fetchone()
    if row is None:
        return None
    slug, acronym, aliases, role_default = row
    variants = sorted({v for v in ([acronym] + list(aliases or [])) if v})
    return {"slug": slug, "acronym": acronym, "variants": variants,
            "role_default": role_default or "sponsor"}


def resolve_agency_scope(con, agency, agency_role="auto", entity="schedule", alias=""):
    """Resolve an agency + role into a SQL WHERE fragment (no '?' placeholders) plus an
    `agency_scope` block to echo. entity in {'schedule','budget'}. `alias` qualifies the
    column reference — pass the table alias when the FROM clause is aliased / self-joined.
    Returns {'where': str, 'agency_scope': {...}} or {'error': str}.
    """
    if agency_role not in ("auto", "sponsor", "managing"):
        return {"error": f"agency_role must be 'auto', 'sponsor', or 'managing' (got {agency_role!r})."}
    info = resolve_agency(con, agency)
    if info is None:
        return {"error": f"Unknown agency '{agency}'. See list_agencies for valid names."}
    if not info["acronym"]:
        return {"error": f"'{agency}' is not a capital-project agency (no CPD presence)."}
    role = agency_role if agency_role in ("sponsor", "managing") else info["role_default"]
    p = f"{alias}." if alias else ""
    in_list = ", ".join(sql_literal(v) for v in info["variants"])
    notes = []
    if role == "managing" and info["role_default"] == "managing":
        notes.append(f"{info['acronym']} defaults to the builder (managing) view; "
                     "pass agency_role='sponsor' for what it owns.")
    if entity == "schedule":
        if role == "sponsor":
            where = (f"EXISTS (SELECT 1 FROM unnest(string_split({p}sponsor_agency, ',')) "
                     f"AS _s(x) WHERE trim(x) IN ({in_list}))")
        else:
            where = f"{p}managing_agency IN ({in_list})"
    elif entity == "budget":
        if role == "sponsor":
            where = (f"{p}fms_id IN (SELECT fms_id FROM fms_sponsor "
                     f"WHERE sponsor_agency IN ({in_list}))")
            notes.append("Sponsor-scoped budget excludes budget-only lines with no linked "
                         "schedule; multi-sponsor lines appear at full value under each "
                         "owner — do not sum across agencies.")
        else:
            where = f"{p}managing_agency IN ({in_list})"
    else:
        return {"error": "entity must be 'schedule' or 'budget'"}
    return {"where": where,
            "agency_scope": {"agency": info["acronym"], "role": role,
                             "note": " ".join(notes) or None}}
