# src/od_cpd/primer.py
"""The domain primer — the rules every consumer must know before touching the data.

Single source of truth, surfaced through TWO channels:
 1. MCP server `instructions` (server.py) — for clients that pass them to the model.
 2. `dataset_info.domain_rules` (tools/lookup.py) — for clients that drop server
    instructions; the first orienting tool call still delivers the rules.
"""

PRIMER = """\
Every project here is, by city definition, a currently-reportable ACTIVE capital
project. Presence IS the active flag — there is no separate status flag.

"Project" is ambiguous. PID identifies a SCHEDULE (what is built and when); FMS ID
identifies a BUDGET (a funding source). These are MANY-TO-MANY: one FMS ID may fund
several PIDs; one PID may be funded by several FMS IDs. Most are 1:1; ~3% fan out —
when an id resolves to multiple counterparts, LIST ALL, never silently pick one.

Schedule questions (phase, completion, delay) read the schedule side (PID); budget
questions (spend, total budget, over-budget) read the budget side (FMS ID).
managing_agency means the EXECUTOR on schedule rows and the BUDGET-HOLDER on budget
rows — a budget-holder does not necessarily build anything. The 13 real schedule
managers are exactly the distinct managing_agency values on the SCHEDULE side; the
BUDGET side's managing_agency is a superset (~25) whose extra ~12 names are
budget-holders/clients that never manage a schedule, not real managers.

Terminology: report neutral, SIGNED values ("moved 45 days later", "budget grew
$2.1M"). Do not echo loaded words ("slippage", "overrun"); they map to the
INCREASING side of a signed metric (slippage = delayed; overrun = cost growth) and
never include the opposite direction.

RAW tables store every column as text — cast in SQL (e.g. CAST(total_budget AS
DOUBLE)). The whole report publishes 3x/year — periods ending 01/05/09
(Jan/May/Sep) — mandated by the City's Commitment Plan; spend figures appear only
in those periods.

Lifecycle runs Pre-Design -> Design -> Construction Procurement -> Construction ->
Close-out. A SCHEDULE (PID) is reported only from the start of Design through the
end of Construction — Pre-Design and Close-out carry no schedule progression, and
NULL milestones there are suppressed-by-rule, not missing. "Substantial completion"
= the construction end date (actual_construction_end).

A BUDGET (FMS) is reported for as long as its funding line stays active, which
outlives construction — so a finished project (lifecycle_status = 'completed') stays
present, sometimes for years, because its budget line is still open. Completed-but-
present is normal: presence means an active BUDGET line, not active construction —
never read a present project as work-in-progress.

Agency attribution is role-aware. "Agency X's projects" means the SPONSOR (owner) view
(sponsor_agency = X) for normal agencies, but the MANAGING (builder) view for the three
construction-manager agencies DDC/DCAS/EDC (which sponsor almost nothing). Tools take
agency + agency_role ('auto'|'sponsor'|'managing'); they echo an agency_scope block. For
budget questions the sponsor view crosses through fms_sponsor (fms_id -> owner); managing
on budget rows is the budget-holder, not the owner.

Location (borough, community board) keys to the BUDGET LINE, not the PID — fms_location
holds the line-level value. A PID's borough derives from its funding lines: one specific
borough -> that borough; 2+ -> 'Multiple' (the boroughs list carries all); only
citywide-registered lines -> 'Citywide'.

original_budget rows come from a separate first-budget system, not the snapshot cadence.
lifetime_budget_variance.original_budget prefers that adopted amount and echoes
original_budget_source ('adopted' | 'first_snapshot') — state the basis when reporting
"growth since inception". Two budget-variance bases: budget_variance / over_budget =
vs the PREVIOUS reporting period (by design); cumulative_budget_change = latest minus
original. Say which basis you are reporting.

Reporting-period basis: every count, total, or ranking is AS OF a reporting period.
Default to the latest published period and STATE it ("as of 202601"); a comparison
names both periods. fms_location, fms_sponsor, and lifetime_budget_variance are
ALL-HISTORY dimensions (one latest row per line/owner, no reporting_period column) —
use them to ENRICH (join for a line's borough/owner) or to report LIFETIME figures,
never to COUNT a single period's inventory. For a period count, aggregate
raw_project_detail / schedule_history / budget_history filtered to the period. Whatever
the basis, say so.
"""

# The primer as discrete rule strings (one per paragraph) — the machine-readable
# form embedded in dataset_info responses.
DOMAIN_RULES = [p.strip() for p in PRIMER.strip().split("\n\n")]
