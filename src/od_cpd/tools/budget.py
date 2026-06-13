# src/od_cpd/tools/budget.py
from __future__ import annotations

from ..dbio import rows_as_dicts
from ..provenance import provenance_block
from ._common import current_period, direction_of, interpolate_sql
from .agency_scope import resolve_agency_scope

# borough/sponsor are excluded: they live on the schedule edge (M:M double-count
# risk — see docstring). A new value here needs a matching inner-SELECT branch below.
_GROUPABLE = {"managing_agency", "category"}
_METRICS = {"total_budget": "total_budget", "spend": "spend_to_date"}
_METRIC_ERR = "metric must be 'total_budget' or 'spend'"


def budget_breakdown_from(con, group_by="managing_agency", metric="total_budget",
                          period="current", agency=None, agency_role="auto"):
    """Sum a budget metric grouped by ``group_by`` for one reporting period.

    Budget aggregates ALWAYS dedup to distinct (fms_id, managing_agency) before
    summing (spec §6.4). ``category`` grouping joins category_dim at the fms_id
    grain (one category per line — additive, no fan-out). Sponsor/borough cuts
    require joining the schedule edge, which risks M:M double-counting; use
    ``run_sql`` for those richer cuts.
    """
    if group_by not in _GROUPABLE:
        return {"error": f"group_by must be one of {sorted(_GROUPABLE)} (v1)"}
    p = current_period(con, "budget_history") if period == "current" else period
    if p is None:
        return {"error": "No budget data available — run `od-cpd init`."}
    col = _METRICS.get(metric)
    if not col:
        return {"error": _METRIC_ERR}
    inner_where = "reporting_period = ?"
    scope = None
    if agency:
        scope = resolve_agency_scope(con, agency, agency_role, entity="budget")
        if "error" in scope:
            return scope
        inner_where += f" AND {scope['where']}"
    if group_by == "category":
        # category_dim keys on fms_id (one category per line) — the join can't fan out.
        inner = (f"SELECT DISTINCT b.fms_id, b.managing_agency, c.category, b.{col} "
                 f"FROM budget_history b JOIN category_dim c USING (fms_id) "
                 f"WHERE {inner_where}")
    else:
        inner = (f"SELECT DISTINCT fms_id, managing_agency, {col} FROM budget_history "
                 f"WHERE {inner_where}")
    sql = (f"SELECT {group_by}, sum({col}) AS value, count(*) AS n FROM ({inner}) d "
           f"GROUP BY {group_by} ORDER BY value DESC")
    groups = rows_as_dicts(con, sql, [p])
    result = {"groups": groups, "period": p, "metric": metric,
              "provenance": provenance_block(
                  definition=f"sum of {metric} by {group_by}, deduped on composite budget PK",
                  scope={"period": p, "dedup": "(fms_id, managing_agency)",
                         "agency": agency, "agency_role": agency_role},
                  row_count=len(groups), reproduce_sql=interpolate_sql(sql, [p]))}
    if scope is not None:
        result["agency_scope"] = scope["agency_scope"]
    return result


def budget_change_from(con, target, from_period, to_period, metric="total_budget",
                       agency_role="auto"):
    """target = 'agency:DEP' or 'fms:ABC'. Δ of metric between two periods (source LAG-aware)."""
    col = _METRICS.get(metric)
    if not col:
        return {"error": _METRIC_ERR}
    kind, sep, val = target.partition(":")
    if kind not in ("agency", "fms") or not sep or not val:
        return {"error": "target must be 'agency:<NAME>' or 'fms:<FMS_ID>'"}
    scope = None
    label = None
    if kind == "agency":
        scope = resolve_agency_scope(con, val, agency_role, entity="budget")
        if "error" in scope:
            return scope
        filt = scope["where"]
        params = [from_period, to_period]
        if scope["agency_scope"]["role"] == "sponsor":
            label = ("Sponsor set is as-of the latest period, applied to the historical "
                     "periods compared here (a line's owner/linkage may have differed then).")
    else:
        filt = "lower(fms_id) = lower(?)"  # FMS ids stored uppercase; accept any case
        params = [val, from_period, to_period]
    sql = (f"WITH d AS (SELECT DISTINCT fms_id, managing_agency, reporting_period, {col} "
           f"FROM budget_history WHERE {filt} AND reporting_period IN (?, ?)) "
           f"SELECT reporting_period, sum({col}) AS total FROM d GROUP BY reporting_period "
           f"ORDER BY reporting_period")
    rows = rows_as_dicts(con, sql, params)
    by = {r["reporting_period"]: r["total"] for r in rows}
    fv, tv = by.get(from_period), by.get(to_period)
    # Don't fabricate a delta when a period is absent (e.g. off-cadence or before the data starts).
    if fv is None or tv is None:
        change = {"value": None, "direction": None,
                  "note": "One or both periods have no data; cannot compute a change."}
    else:
        d = tv - fv
        change = {"value": d, "direction": direction_of(d, kind="budget")}
    result = {"target": target, "from_period": from_period, "to_period": to_period,
              "from_value": fv, "to_value": tv, "change": change, "label": label,
              "provenance": provenance_block(definition=f"Δ {metric} {from_period}→{to_period}",
                  scope={"target": target, "dedup": "(fms_id, managing_agency)",
                         "agency_role": agency_role},
                  row_count=len(rows), reproduce_sql=interpolate_sql(sql, params))}
    if scope is not None:
        result["agency_scope"] = scope["agency_scope"]
    return result
