# src/od_cpd/materialize.py
from __future__ import annotations

import duckdb

from . import categories, data_dictionary
from .config import CADENCE_MONTHS


def _cadence_filter(col: str = "p") -> str:
    months = ", ".join(f"'{m}'" for m in CADENCE_MONTHS)
    return f"right({col}, 2) IN ({months})"


def _normalized_phase_expr(col: str = "current_phase") -> str:
    return (f"regexp_replace(lower(trim(replace(replace({col},'(',''),')',''))), "
            "'\\s+', ' ', 'g')")


def build_normalized(con: duckdb.DuckDBPyConnection) -> None:
    # 1) schedule_budget_link — M:M edge from fb86 PID rows, in-cadence, + cardinality.
    con.execute(f"""
        CREATE OR REPLACE TABLE schedule_budget_link AS
        WITH edge AS (
            SELECT DISTINCT reporting_period AS p, pid, fms_id, managing_agency,
                   TRY_CAST(total_budget AS DOUBLE) AS commitments
            FROM raw_project_detail
            WHERE pid IS NOT NULL AND fms_id IS NOT NULL AND {_cadence_filter('p')}
        )
        SELECT p AS reporting_period, pid, fms_id, managing_agency, commitments,
               count(*) OVER (PARTITION BY p, pid) AS n_budgets_for_pid,
               count(*) OVER (PARTITION BY p, fms_id, managing_agency) AS n_pids_for_budget
        FROM edge
    """)

    # 2) schedule_history — fb86 spine (one row per pid,period) ⨝ 95tx variance.
    # phase_score: lifecycle precedence across a PID's FMS rows (deterministic, vs any_value).
    p3 = _normalized_phase_expr("current_phase")
    con.execute(f"""
        CREATE OR REPLACE TABLE schedule_history AS
        WITH fb AS (
            SELECT reporting_period AS p, pid,
                   any_value(managing_agency) AS managing_agency,
                   any_value(sponsor_agency)  AS sponsor_agency,
                   any_value(agency_project_name) AS agency_project_name,
                   any_value(agency_project_description) AS agency_project_description,
                   -- borough/community_board are LINE-keyed:
                   -- carry the distinct set; the scalar is derived below.
                   COALESCE(list(DISTINCT borough ORDER BY borough)
                            FILTER (WHERE borough IS NOT NULL), []) AS boroughs,
                   COALESCE(list(DISTINCT borough ORDER BY borough)
                            FILTER (WHERE borough IS NOT NULL AND borough <> 'Citywide'),
                            []) AS specific_boroughs,
                   COALESCE(list(DISTINCT community_board ORDER BY community_board)
                            FILTER (WHERE community_board IS NOT NULL), []) AS community_boards,
                   -- current_phase is PAIR-keyed (86 historical PID-periods disagree across
                   -- a PID's FMS rows). Parenthesized values are
                   -- no-schedule REASONS, not phases: prefer a real phase, deterministic tiebreak.
                   COALESCE(min(current_phase) FILTER (WHERE current_phase NOT LIKE '(%'),
                            min(current_phase)) AS current_phase,
                   max(CASE
                       WHEN {p3} IN ('completed','close-out','closeout') THEN 3
                       WHEN {p3} IN ('cancelled','terminated','withdrawn','defaulted') THEN 2
                       ELSE 1 END) AS phase_score,
                   max(TRY_CAST(actual_construction_end AS DATE)) AS actual_construction_end,
                   max(TRY_CAST(actual_design_start AS DATE))     AS actual_design_start,
                   max(TRY_CAST(forecast_completion AS DATE))     AS forecast_completion
            FROM raw_project_detail
            WHERE pid IS NOT NULL AND {_cadence_filter('p')}
            GROUP BY reporting_period, pid
        ),
        sx AS (
            SELECT reporting_period AS p, pid,
                   any_value(TRY_CAST(variance_day AS BIGINT)) AS variance_day,
                   any_value(completion_date) AS completion_date,
                   any_value(completion_date_type) AS completion_date_type,
                   any_value(reason_for_forecast_completion_change) AS reason_for_delay
            FROM raw_schedule_history
            WHERE pid IS NOT NULL AND {_cadence_filter('reporting_period')}
            GROUP BY reporting_period, pid
        )
        SELECT fb.pid, fb.p AS reporting_period, fb.managing_agency, fb.sponsor_agency,
               fb.agency_project_name, fb.agency_project_description,
               -- scalar borough: specific-beats-Citywide (owner ruling 2026-06-12).
               -- 1 specific borough -> it (a citywide umbrella line doesn't relocate
               -- the work); 2+ specific -> 'Multiple'; only Citywide -> 'Citywide'.
               CASE WHEN len(fb.specific_boroughs) = 1 THEN fb.specific_boroughs[1]
                    WHEN len(fb.specific_boroughs) > 1 THEN 'Multiple'
                    WHEN len(fb.boroughs) > 0 THEN 'Citywide'
                    END AS borough,
               fb.boroughs, fb.community_boards, fb.current_phase,
               {_normalized_phase_expr('fb.current_phase')} AS phase_norm,
               fb.actual_construction_end, fb.actual_design_start, fb.forecast_completion,
               sx.variance_day,
               CASE WHEN sx.variance_day > 0 THEN 'later'
                    WHEN sx.variance_day < 0 THEN 'earlier'
                    WHEN sx.variance_day = 0 THEN 'unchanged' END AS direction,
               sx.completion_date, sx.completion_date_type, sx.reason_for_delay,
               CASE
                 WHEN fb.actual_construction_end IS NOT NULL THEN 'completed'
                 WHEN fb.phase_score = 3 THEN 'completed'
                 WHEN fb.phase_score = 2 THEN 'cancelled'
                 ELSE 'in_progress'
               END AS lifecycle_status,
               (fb.managing_agency = fb.sponsor_agency) AS self_managed
        FROM fb LEFT JOIN sx ON fb.pid = sx.pid AND fb.p = sx.p
    """)

    # 3) budget_history — qj5n SNAPSHOT rows only, in-cadence, typed.
    # Rows with spend_to_date IS NULL are NOT snapshots: they are original-budget
    # records imported from a separate first-budget source, stamped with the
    # adoption month (any calendar month).
    # They are captured in original_budget below — never merged into snapshots
    # (the old max() dedup hybridized them) and never cadence-dropped. With them
    # excluded, groups are single-row in practice; max() stays only as defensive
    # canonicalization should the source ever emit a true same-period duplicate.
    con.execute(f"""
        CREATE OR REPLACE TABLE budget_history AS
        SELECT fms_id, managing_agency, year_month_reported AS reporting_period,
               max(TRY_CAST(total_budget AS DOUBLE)) AS total_budget,
               max(TRY_CAST(spend_to_date AS DOUBLE)) AS spend_to_date,
               max(TRY_CAST(spend_to_date_1 AS DOUBLE)) AS spend_pct,
               max(TRY_CAST(budget_variance AS DOUBLE)) AS budget_variance,
               max(TRY_CAST(budget_variance_1 AS DOUBLE)) AS budget_variance_pct
        FROM raw_budget_history
        WHERE {_cadence_filter('year_month_reported')} AND spend_to_date IS NOT NULL
        GROUP BY fms_id, managing_agency, year_month_reported
    """)

    # 3b) original_budget — the adopted/first-recorded budget per line, from the
    # NULL-spend rows. One row per line upstream; min() guards defensively.
    con.execute("""
        CREATE OR REPLACE TABLE original_budget AS
        SELECT fms_id, managing_agency,
               min(year_month_reported) AS recorded_period,
               arg_min(TRY_CAST(total_budget AS DOUBLE), year_month_reported)
                   AS original_budget
        FROM raw_budget_history
        WHERE spend_to_date IS NULL
        GROUP BY fms_id, managing_agency
    """)

    # 4) project_budget_fy — gyhf, typed.
    con.execute(f"""
        CREATE OR REPLACE TABLE project_budget_fy AS
        SELECT fms_id, managing_agency, reporting_period,
               TRY_CAST(fiscal_year AS INTEGER) AS fiscal_year,
               TRY_CAST(city AS DOUBLE) AS city,
               TRY_CAST(non_city AS DOUBLE) AS non_city,
               TRY_CAST(spend AS DOUBLE) AS spend
        FROM raw_budget_fy
        WHERE {_cadence_filter('reporting_period')}
    """)


def build_analytics(con: duckdb.DuckDBPyConnection) -> None:
    # pid_funding — distinct (fms_id, managing_agency) commitments per pid at its latest period.
    con.execute("""
        CREATE OR REPLACE TABLE pid_funding AS
        WITH latest AS (
            SELECT pid, max(reporting_period) AS p FROM schedule_budget_link GROUP BY pid
        ),
        dedup AS (
            SELECT DISTINCT l.pid, l.fms_id, l.managing_agency, l.commitments
            FROM schedule_budget_link l JOIN latest ON l.pid = latest.pid
                 AND l.reporting_period = latest.p
        )
        SELECT pid,
               count(*) AS n_linked_budgets,
               sum(commitments) AS attributed_budget,
               list({'fms_id': fms_id, 'managing_agency': managing_agency}) AS linked_budgets
        FROM dedup GROUP BY pid
    """)

    # latest_project_state — latest snapshot per pid + funding attribution.
    # community_boards is intentionally NOT carried here: CB analysis is line-level
    # (fms_location); only the borough pair (scalar + list) is tool-facing per PID.
    con.execute("""
        CREATE OR REPLACE TABLE latest_project_state AS
        WITH s AS (
            SELECT * FROM schedule_history
            QUALIFY row_number() OVER (PARTITION BY pid ORDER BY reporting_period DESC) = 1
        )
        SELECT s.pid, s.agency_project_name, s.reporting_period,
               s.lifecycle_status, s.current_phase, s.phase_norm,
               s.managing_agency, s.sponsor_agency, s.self_managed, s.borough, s.boroughs,
               s.completion_date, s.completion_date_type, s.forecast_completion,
               s.actual_construction_end,
               s.variance_day AS period_variance_days, s.direction, s.reason_for_delay,
               COALESCE(f.n_linked_budgets, 0) AS n_linked_budgets,
               COALESCE(f.attributed_budget, 0.0) AS attributed_budget,
               f.linked_budgets
        FROM s LEFT JOIN pid_funding f ON s.pid = f.pid
    """)

    # fms_location — ALL-HISTORY dim (no reporting_period; mirror in
    # tools/sql.py:_ALL_HISTORY_TABLES). Line-level location dimension. borough/community_board are
    # attributes of (fms_id, managing_agency) (verified: 0 within-period
    # violations in all periods); take the line's latest in-cadence row
    # so the pair stays internally consistent. This is the budget side's native
    # location — borough-scoped budget questions need no PID crossing.
    con.execute(f"""
        CREATE OR REPLACE TABLE fms_location AS
        SELECT fms_id, managing_agency, borough, community_board
        FROM raw_project_detail
        WHERE fms_id IS NOT NULL AND {_cadence_filter('reporting_period')}
        QUALIFY row_number() OVER (PARTITION BY fms_id, managing_agency
                                   ORDER BY reporting_period DESC) = 1
    """)

    # cumulative_schedule_variance — per pid; guard NULL + absurd outliers.
    con.execute("""
        CREATE OR REPLACE TABLE cumulative_schedule_variance AS
        SELECT pid,
               sum(CASE WHEN variance_day BETWEEN -36500 AND 36500 THEN variance_day END)
                   AS cumulative_variance_days,
               max_by(variance_day, reporting_period) AS period_variance_days,
               max_by(forecast_completion, reporting_period) AS latest_forecast_completion
        FROM schedule_history
        WHERE variance_day IS NOT NULL
        GROUP BY pid
    """)

    # lifetime_budget_variance — per (fms_id, managing_agency); source LAG variance, not
    # recomputed. ALL-HISTORY dim (no reporting_period; mirror in
    # tools/sql.py:_ALL_HISTORY_TABLES).
    # original_budget prefers the ADOPTED amount (original_budget table, a
    # separate first-budget system) and falls back to the first in-window snapshot;
    # original_budget_source says which ('adopted' | 'first_snapshot') so answers can
    # state the basis of "growth since inception".
    con.execute("""
        CREATE OR REPLACE TABLE lifetime_budget_variance AS
        WITH ordered AS (
            SELECT fms_id, managing_agency, reporting_period, total_budget, spend_to_date,
                   spend_pct, budget_variance,
                   first_value(total_budget) OVER w AS first_snapshot_budget,
                   last_value(total_budget)  OVER w AS latest_budget,
                   last_value(spend_to_date) OVER w AS latest_spend,
                   last_value(spend_pct)     OVER w AS latest_spend_pct,
                   last_value(budget_variance) OVER w AS latest_variance,
                   row_number() OVER (PARTITION BY fms_id, managing_agency
                                      ORDER BY reporting_period DESC) AS rn
            FROM budget_history
            WINDOW w AS (PARTITION BY fms_id, managing_agency ORDER BY reporting_period
                         ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
        )
        SELECT o.fms_id, o.managing_agency,
               COALESCE(ob.original_budget, o.first_snapshot_budget) AS original_budget,
               CASE WHEN ob.original_budget IS NOT NULL
                    THEN 'adopted' ELSE 'first_snapshot' END AS original_budget_source,
               o.first_snapshot_budget,
               o.latest_budget,
               (o.latest_budget - COALESCE(ob.original_budget, o.first_snapshot_budget))
                   AS cumulative_budget_change,
               o.latest_spend AS spend_to_date, o.latest_spend_pct AS spend_pct,
               o.latest_variance AS budget_variance,
               -- over_budget is LAST-PERIOD basis BY DESIGN (owner ruling 2026-06-12):
               -- it flags growth vs the previous reporting period. Cumulative growth
               -- since inception is cumulative_budget_change (> 0).
               COALESCE(o.latest_variance > 0, FALSE) AS over_budget
        FROM ordered o LEFT JOIN original_budget ob
          ON o.fms_id = ob.fms_id AND o.managing_agency = ob.managing_agency
        WHERE o.rn = 1
    """)

    # agency_rollup_by_period — dedup budgets on the composite PK (spec §6.4).
    con.execute("""
        CREATE OR REPLACE TABLE agency_rollup_by_period AS
        WITH sched AS (
            SELECT managing_agency, reporting_period,
                   count(*) AS pid_count,
                   count(*) FILTER (WHERE lifecycle_status = 'completed') AS completed_count,
                   count(*) FILTER (WHERE variance_day > 0) AS delayed_count
            FROM schedule_history GROUP BY 1, 2
        ),
        bud AS (
            SELECT managing_agency, reporting_period,
                   sum(total_budget) AS total_budget, sum(spend_to_date) AS total_spend
            FROM (SELECT DISTINCT fms_id, managing_agency, reporting_period,
                         total_budget, spend_to_date FROM budget_history) d
            GROUP BY 1, 2
        )
        SELECT COALESCE(s.managing_agency, b.managing_agency) AS managing_agency,
               COALESCE(s.reporting_period, b.reporting_period) AS reporting_period,
               COALESCE(s.pid_count, 0) AS pid_count,
               COALESCE(s.completed_count, 0) AS completed_count,
               COALESCE(s.delayed_count, 0) AS delayed_count,
               b.total_budget, b.total_spend
        FROM sched s FULL OUTER JOIN bud b
          ON s.managing_agency = b.managing_agency AND s.reporting_period = b.reporting_period
    """)

    # fms_sponsor — ALL-HISTORY dim (no reporting_period; mirror in
    # tools/sql.py:_ALL_HISTORY_TABLES).
    # Budget-side owner bridge: fms_id -> sponsor_agency. Reads each PID's
    # current link set from latest_project_state.linked_budgets (pid_funding's product),
    # so the "current links = the PID's own latest link snapshot" rule has ONE definition.
    # A global-latest-period filter would drop every line whose links last appeared in
    # an earlier period (~200 still-budgeted lines), silently shrinking the sponsor lens.
    # Composite comma-joined sponsor strings (a few PIDs, e.g. 'DOT, DPR') are split
    # into atomic rows so each owner is matchable.
    # NB: must run after latest_project_state (above). See docs/FEATURES.md §4.
    con.execute("""
        CREATE OR REPLACE TABLE fms_sponsor AS
        SELECT DISTINCT b.fms_id, trim(atom) AS sponsor_agency
        FROM latest_project_state s
        CROSS JOIN unnest(s.linked_budgets) AS _l(b)
        CROSS JOIN unnest(string_split(s.sponsor_agency, ',')) AS u(atom)
        WHERE trim(atom) <> ''
    """)


def materialize_all(con: duckdb.DuckDBPyConnection) -> None:
    build_normalized(con)
    build_analytics(con)
    categories.build_category_dim(con)
    data_dictionary.build_column_dict(con)
