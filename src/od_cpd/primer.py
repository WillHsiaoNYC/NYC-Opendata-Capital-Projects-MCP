"""Canonical rules delivered at connection, discovery and call time.

Keep stable IDs and scope rules to the tools whose answers need them. Instructions,
tool descriptions and success envelopes use the same text; clients need not read
repository files or call dataset_info before receiving relevant guidance.
"""

RULES = {
    "relationships": (
        '"Project" is ambiguous: PID identifies a SCHEDULE; FMS ID identifies a BUDGET. '
        "They are MANY-TO-MANY: a budget can fund several PIDs and a PID can have several budgets. "
        "LIST ALL linked counterparts; never silently select one. One linked counterpart does "
        "not establish a 1:1 relationship in the reverse direction. A budget without a PID is "
        "normal before Design and for lines that do not require schedules; absence alone does "
        "not establish missing data. Source-only schedules can also lack dashboard funding links."
    ),
    "grain": (
        "Schedule questions use PID; budget questions use the BUDGET LINE key "
        "(managing_agency, fms_id). The same FMS ID under different holders is distinct lines. "
        "Schedule history is PID x reporting_period; budget history is budget line x reporting_period; "
        "fiscal-year budgets also key on fiscal_year. The schedule source has no fms_id; budget "
        "sources have no pid. The combined source repeats rows across "
        "PID-budget links and location splits: deduplicate at the requested entity grain before "
        "counting or summing. Never compare budgets using fms_id alone."
    ),
    "presence": (
        "Presence in a selected reporting snapshot means reportable at that period. There is "
        "no separate active flag. Historical presence or a latest-known row does not prove "
        "presence in the current complete snapshot. A completed project can retain an open "
        "budget line for years; presence does not mean construction is in progress."
    ),
    "period_basis": (
        "Reporting-period basis: state the returned period for every count, total or ranking "
        "and both periods for comparisons. Period aggregates default to the latest complete "
        "snapshot, which may precede a partially published newer period. fms_location, fms_sponsor "
        "and lifetime_budget_variance are all-history dimensions without reporting_period: use "
        "them for enrichment or lifetime figures, never as a single period's inventory."
    ),
    "population": (
        "Listings/rankings default to population_scope='latest_known' (each entity's own latest "
        "observation). Use population_scope='current' for the selected complete snapshot. State "
        "the population_scope and observation period; respect present_in_current_snapshot. "
        "Detail tools use latest-known state and each anchor's latest available link period; "
        "these links are not proof of presence in the current complete snapshot."
    ),
    "agency_roles": (
        "Agency attribution is role-aware: an agency's projects mean its sponsor (owner) view, "
        "except DDC/DCAS/EDC default to managing (builder). State the returned agency_scope. "
        "managing_agency is the executor on schedules and the budget-holder on budgets; a "
        "budget-only holder is not a schedule executor. list_agencies exposes is_schedule_executor. "
        "For sponsor-scoped budget totals, use a semi-join to fms_sponsor; a value-bearing join "
        "can multiply lines. Co-owner totals can overlap and must not be added together."
    ),
    "categories": (
        "Classify with the curated category_dim, not project-name searches: specific ten-year "
        "labels/FMS prefixes precede sponsor routing, then generic facility keywords and Other. "
        "Categories key on (managing_agency, fms_id); institution owner rules can use all-history "
        "ownership. Schedule category_scope='current' uses each PID's current funding links; "
        "'all_history' includes former links. A PID can count in multiple categories, so schedule "
        "category counts are non-additive; each budget line has one category."
    ),
    "signed_values": (
        'Report neutral, SIGNED changes: "moved 45 days later" or "budget decreased $2M". '
        "Do not echo loaded terms in the answer. "
        '"slippage" means positive schedule change and "overrun" means positive budget growth; '
        "neither includes the decreasing side. Preserve the returned direction and metric basis."
    ),
    "budget_baseline": (
        "budget_variance is change from the previous reporting period; cumulative_budget_change "
        "is latest minus original. State which basis is used. original_budget prefers the adopted "
        "first budget, with first_snapshot as fallback: disclose original_budget_source. Adoption "
        "months are calendar months from a separate first-budget system, not reporting snapshots."
    ),
    "lifecycle": (
        "Lifecycle: Pre-Design -> Design -> Construction Procurement -> Construction -> Close-out. "
        "Schedule progression is reported from Design through Construction. Forecasts and most "
        "actual milestones can be suppressed outside those phases; actual_construction_end is "
        "the exception and means substantial completion. NULL milestones need not mean missing "
        "data. Budget spend%=100 does not prove completion. forecast_past_due is evaluated as of "
        "the observation period, not today's date."
    ),
    "schedule_coverage": (
        "Schedule totals and cumulative variance use dashboard-aligned schedule_history. "
        "source_schedule_history retains native observations absent from that population; "
        "schedule_source_coverage reconciles them. State the schedule universe and cumulative "
        "basis. Parenthesized phases are no-schedule reasons. Respect excluded variance artifacts "
        "and missing/invalid-duration counts; forward duration statistics exclude reversed dates."
    ),
    "location": (
        "Location belongs to the BUDGET LINE, represented by fms_location. A PID inherits its "
        "funding-line boroughs: one specific borough takes precedence over Citywide, multiple "
        "specific boroughs yield 'Multiple', and only Citywide lines yield 'Citywide'. Preserve "
        "the boroughs list when several boroughs apply."
    ),
    "funding_totals": (
        "attributed_budget counts a funding line fully on every PID it funds; it is not an "
        "allocated share or an independent project cost. Summing it across PIDs can double-count "
        "shared funding. For the portfolio's distinct funding total use summary.line_budget_total, "
        "which counts each (managing_agency, fms_id) once. State which budget basis is reported."
    ),
    "resolution": (
        "Call resolve_project_reference first for named-project questions. Matches identify "
        "candidate entities, not funding relationships: inspect linked_budgets/linked_schedules "
        "in the detail tools. Preserve multiple candidates rather than silently selecting one. "
        "Follow pagination next_offset when more matches are needed; disclose truncation when "
        "reporting an incomplete list."
    ),
    "freshness": (
        "Reporting snapshots publish three times yearly (Jan/May/Sep, YYYYMM ending 01/05/09). "
        "Reporting periods, source revisions and ingestion timestamps are different clocks. "
        "dataset_info is local-only metadata, not an upstream freshness check. Use its available "
        "periods and complete snapshot information when choosing a comparison."
    ),
    "sql_usage": (
        "Prefer purpose-built tools for supported questions. For custom SQL, use describe_table "
        "for grain/keying and describe_field for field definitions, then prefer typed tables. "
        "Raw mirrors are VARCHAR and need casts. run_sql enforces read-only access, not correct "
        "analytical grain: the caller must prevent join fan-out and choose the period/population. "
        "Disclose truncated results; use CSV/XLSX for the full result when needed."
    ),
}

DOMAIN_RULES = list(RULES.values())
PRIMER = "\n\n".join(DOMAIN_RULES)

# Explicit coverage makes a new or renamed tool fail registration until its
# interpretation requirements have been considered. No blanket primer per tool.
TOOL_RULE_IDS = {
    "dataset_info": ("freshness", "period_basis"),
    "list_agencies": ("agency_roles", "presence"),
    "list_categories": ("categories", "grain", "period_basis"),
    "describe_field": ("grain", "lifecycle"),
    "describe_table": ("grain", "period_basis", "sql_usage"),
    "resolve_project_reference": ("resolution", "relationships", "grain", "presence"),
    "get_project_schedule": ("relationships", "population", "lifecycle", "funding_totals",
                             "signed_values", "schedule_coverage", "location"),
    "get_project_budget": ("relationships", "grain", "population", "budget_baseline", "lifecycle",
                           "signed_values"),
    "get_project_history": ("relationships", "grain", "population", "budget_baseline",
                            "signed_values", "schedule_coverage", "lifecycle"),
    "schedule_breakdown": ("grain", "period_basis", "agency_roles", "categories",
                           "signed_values", "schedule_coverage", "presence"),
    "schedule_changes": ("period_basis", "signed_values", "schedule_coverage", "lifecycle", "agency_roles"),
    "delay_reason_stats": ("period_basis", "agency_roles", "schedule_coverage", "signed_values"),
    "budget_breakdown": ("grain", "period_basis", "agency_roles", "categories"),
    "budget_change": ("grain", "period_basis", "agency_roles", "budget_baseline", "signed_values"),
    "rank_projects": ("relationships", "grain", "population", "presence", "agency_roles",
                      "categories", "funding_totals", "budget_baseline", "signed_values",
                      "schedule_coverage", "lifecycle"),
    "project_duration_stats": ("lifecycle", "schedule_coverage", "population"),
    "project_portfolio": ("relationships", "population", "presence", "agency_roles", "categories",
                          "funding_totals", "lifecycle", "location", "signed_values", "schedule_coverage"),
    "run_sql": ("sql_usage", "relationships", "grain", "period_basis", "population", "agency_roles",
                "categories", "funding_totals", "budget_baseline", "schedule_coverage",
                "signed_values", "lifecycle", "location", "presence"),
}


def rules_for_tool(name: str) -> list[dict[str, str]]:
    return [{"id": key, "text": RULES[key]} for key in TOOL_RULE_IDS[name]]
