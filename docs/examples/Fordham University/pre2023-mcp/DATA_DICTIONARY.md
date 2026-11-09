# Historical CPD data dictionary — code-only 1.1.0

The definitions below accompany software only. The course Excel is supplied separately; source rows and per-field source-null counts are available through the MCP after a validated local import. Grain: legacy PID × reporting period.

## Interpretation rules

- Dataset is cpd-pre2023: 201309–202301, frozen historical data, never current NYC inventory. When both MCPs are enabled, disambiguate the requested dataset.
- One observation is legacy_pid × reporting_period. Legacy PIDs collide with unrelated modern PIDs; no automatic cross-era joins or concatenation.
- Financial values belong to the PID observation. FMS tokens are references only; do not copy totals onto tokens or infer allocation. Shared references mean PID sums are not proven distinct funding totals.
- Period means the explicitly returned observation period. Latest within this dataset is 202301. Latest-known rows may be older; absence is not proof of completion.
- Source legacy cost variance is not period forecast change. Its component-level fallback formula is inferred, not verified; missing component detail prevents reconstruction.
- Percentages are stored as ratios: multiply by 100 for display. Missing is not zero. First/unknown previous forecast gives null period change.
- Observed milestone dates on/before day 15 of their own reporting month are classified actual; later dates forecast. This is the owner-agreed analytical convention, not a source actual-date flag. Plan dates remain plans.
- Date quality policy excludes non-calendar values and years outside 1900–2100 from typed milestones, retains raw values, and flags reversed intervals. Do not silently repair source dates or use flagged intervals as valid durations.
- Preliminary budget revisions are preserved. Original amount is audit-only. Status and phase are independent; substantial completion is not final close-out.
- Do not import modern agency/category/suppression rules, assume a regular cadence, sum snapshots as separate projects, or enrich historical rows with current attributes without an explicit crosswalk and provenance.

## Inferred legacy cost-variance formula

Working hypothesis — NOT VERIFIED; NOT used to overwrite reported values:
For each underlying cost component, baseline = original amount when available/nonzero,
otherwise preliminary budget. Project baseline = sum of component baselines.
Cost variance ($) = current forecast − project baseline.
Cost variance (%) = cost variance ($) / project baseline × 100.
Stored ratios omit × 100. A zero denominator produces no calculated ratio.
Component detail is absent, so this formula cannot be executed faithfully from exported totals.
Period forecast change is separately calculated as current forecast minus the preceding available observation.
Preserve reported legacy variance; do not substitute this hypothesis for source values.

## All 40 source fields

| Source header | SQL field | Units/type | Meaning and confidence |
|---|---|---|---|
| PID | `legacy_pid` | TEXT | **Source identifier.** Historical schedule/project identifier. Text; NOT interchangeable with current CPD PID. |
| Agency | `agency` | TEXT | **Source label; role unconfirmed.** Agency reported on the legacy project. Do not assume separate sponsor or FMS budget-holder roles. |
| Project Name | `project_name` | TEXT | **Source label.** Project name as reported in this observation; not a unique identifier. |
| PERIOD | `reporting_period` | TEXT | **Source observation.** YYYYMM, normalized from YYYY MM. Irregular observed periods; not restricted to Jan/May/Sep. |
| Status | `status` | TEXT | **Source status; normalized spelling.** Explicit project status. On Hold becomes On-Hold. Substantial Completion remains distinct from Completed. No status inferred from dates. |
| FMS ID | `fms_ids_raw` | TEXT | **Source reference list.** Original comma-separated FMS references. Amounts are NOT allocated to individual tokens. See fms_references. |
| Current Phase | `current_phase` | TEXT | **Source phase; normalized spelling.** Scoping / Planning capitalization normalized; Close-Out becomes Close-out. Keep status separate. |
| Slippage (days) from previous period | `reported_slippage_days` | days | **Empirically supported interpretation.** Signed change in construction-end observation from preceding available PID observation. Matches all 5,700 testable adjacent date pairs; preserve source exceptions/missing values. |
| Current Phase Variance (days) | `reported_phase_variance_days` | days | **Unconfirmed legacy measure.** Reported current-phase variance in days. Baseline and phase-transition calculation unknown; not the same as period construction-end change. |
| Current Phase Variance (%) | `reported_phase_variance_ratio` | fractional ratio | **Unconfirmed legacy measure.** Reported fractional phase variance. Denominator unconfirmed; do not reconstruct durations from this ratio. |
| Deesign Plan Design Start | `design_plan_design_start` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan design start. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Design End | `design_plan_design_end` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan design end. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Procurement Start | `design_plan_procurement_start` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan procurement start. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Procurement End | `design_plan_procurement_end` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan procurement end. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Construction Start | `design_plan_construction_start` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan construction start. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Construction End | `design_plan_construction_end` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan construction end. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Close Out Start | `design_plan_closeout_start` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan closeout start. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Deesign Plan Close Out End | `design_plan_closeout_end` | ISO date | **Source plan; baseline rules unconfirmed.** Planned design plan closeout end. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Const. Plan Construction Start | `construction_plan_construction_start` | ISO date | **Source plan; baseline rules unconfirmed.** Planned construction plan construction start. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Const. Plan Construction End | `construction_plan_construction_end` | ISO date | **Source plan; baseline rules unconfirmed.** Planned construction plan construction end. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Const. Plan Close Out Start | `construction_plan_closeout_start` | ISO date | **Source plan; baseline rules unconfirmed.** Planned construction plan closeout start. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Const. Plan Close Out End | `construction_plan_closeout_end` | ISO date | **Source plan; baseline rules unconfirmed.** Planned construction plan closeout end. Retain design-plan and construction-plan baselines separately; no actual/forecast classification. |
| Actual / Forecast  Scoping Start | `observed_scoping_start` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast scoping start. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Scoping End | `observed_scoping_end` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast scoping end. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Design Start | `observed_design_start` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast design start. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Design End | `observed_design_end` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast design end. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Proc. Start | `observed_procurement_start` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast procurement start. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Proc. End | `observed_procurement_end` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast procurement end. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Const. Start | `observed_construction_start` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast construction start. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Const. End | `observed_construction_end` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast construction end. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Close out Start | `observed_closeout_start` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast closeout start. Date type derived per observation using day 15 cutoff, never stability or today. |
| Actual / Forecast Close out End | `observed_closeout_end` | ISO date | **Owner-agreed analytical interpretation.** Mixed actual/forecast closeout end. Date type derived per observation using day 15 cutoff, never stability or today. |
| Cost Variance ($) | `reported_cost_variance` | USD | **Source value; formula inferred.** Preserved reported legacy cost variance in USD. Likely forecast minus component-level baseline. NOT period-to-period forecast change. See reconstructed formula. |
| Cost Variance (%) | `reported_cost_variance_ratio` | fractional ratio | **Source value; formula inferred.** Fractional ratio: 0.25 means 25%. Usually reported variance / implied baseline. See reconstructed formula; values are not replaced. |
| Preliminary Budget Total | `preliminary_budget` | USD | **Owner-confirmed meaning.** First budget forecast when project starts, in USD. Source values sometimes change; retain each observation and flag baseline revisions. Earliest observed row need not be project inception. |
| Orig. Amount Total | `original_amount` | USD | **Audit-only; precise meaning unconfirmed.** Preserved source original amount, in USD; frequently zero. Excluded from default student financial summaries. Used only to investigate legacy baseline hypothesis; not automatically an adopted budget. |
| Change Orders Total | `change_orders` | USD | **Source label; scope unconfirmed.** Reported change-order total in USD, including negative values. Blank is unknown, not zero. Do not assume forecast = original + change orders. |
| Current Forecast  Total | `current_forecast` | USD | **Owner-confirmed meaning.** Updated total budget forecast at data collection, in USD, at PID-period grain. Not an FMS-line budget. |
| Expenditure (spent to date)Total | `expenditure` | USD | **Source label.** Reported cumulative expenditure/spent to date in USD at PID-period grain. Accounting equivalence to modern FMS liquidations unconfirmed. |
| Spent to Date (%) | `reported_spent_ratio` | fractional ratio | **Empirically supported interpretation.** Fractional expenditure/current-forecast ratio. Matches 7,106/7,112 testable nonzero forecasts at 1e-8 tolerance. Preserve exceptions, negatives, and values above 1. |

## Derived fields

| Field | Definition |
|---|---|
| `period_forecast_change` | Current forecast minus preceding available observation forecast; first/missing operand gives NULL. |
| `period_forecast_change_ratio` | Period forecast change / previous forecast; zero or missing denominator gives NULL. |
| `change_from_preliminary` | Current forecast minus preliminary budget reported in this same row, including source revisions. |
| `change_from_preliminary_ratio` | Change from preliminary divided by same-row preliminary budget; zero/missing denominator gives NULL. |
| `implied_legacy_baseline` | Current forecast minus reported legacy cost variance; algebraic inference, not validated independent baseline. |
| `previous_reporting_period` | Immediately preceding available observation for this legacy PID. |
| `previous_forecast` | Forecast at preceding available observation; do not skip null-valued rows. |
| `previous_preliminary_budget` | Preliminary budget at preceding available observation. |
| `preliminary_baseline_changed` | 1 if adjacent available preliminary budgets differ by more than $0.01, 0 if not, NULL if either missing. |
| `skipped_available_snapshots` | Number of workbook snapshot periods between adjacent PID observations; NULL on first observation. |
| `previous_construction_end` | Typed mixed construction-end milestone at preceding available observation. |
| `construction_end_change_days` | Current typed construction-end date minus preceding typed end date in calendar days; NULL if either missing/invalid. May cross actual/forecast states. |
| `date_types_json` | Per observed milestone: actual on/before day 15 of reporting month, forecast after, unknown for missing/invalid. Plans not classified. |
| `quality_flags_json` | Review flags; see interpretation rules and DATA_DICTIONARY.md. No wholesale record deletion. |
| `source_row` | Original Excel row number including header row. |

## SQL tables

`observations`: all 40 typed fields plus source row, date types and quality flags. `raw_observations`: original headers/cells, with datetime/time tags, at workbook-row grain. `fms_references`: PID × period × token ordinal; reference amounts are not recoverable. `reporting_periods`: available workbook periods. `project_history`: observations and derived prior-observation measures. `latest_known`: one latest-known observation per legacy PID.

## Limits

No independent FMS budgets, sponsor attribution, geography, fiscal-year funding or modern PID crosswalk is supplied. Source cells and analytical conventions remain distinct. SQLite REAL preserves Excel double precision; financial comparisons use cents tolerances. Invalid typed dates retain their raw source value and quality flag. No blanket exclusion or modern-data substitution occurs.
