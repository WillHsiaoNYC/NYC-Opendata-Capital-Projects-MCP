"""MCP input choices and typed success envelopes; variable SQL cells stay open."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AgencyRole = Literal["auto", "sponsor", "managing"]
PopulationScope = Literal["latest_known", "current"]
CategoryScope = Literal["current", "all_history"]
RowLimit = Annotated[int, Field(strict=True, ge=1, le=500)]
Offset = Annotated[int, Field(strict=True, ge=0)]
Identifier = Annotated[str, Field(min_length=1)]
ScheduleGroup = Literal["managing_agency", "sponsor_agency", "borough", "phase_norm", "lifecycle_status", "category"]
ScheduleMetric = Literal["count", "schedule_variance"]
Statistic = Literal["count", "mean", "median", "sum", "min", "max"]
BudgetGroup = Literal["managing_agency", "category"]
BudgetMetric = Literal["total_budget", "spend"]
Milestone = Literal["actual_design_start", "actual_construction_end"]
DurationGroup = Literal["managing_agency", "borough", "lifecycle_status"]
Lifecycle = Literal["in_progress", "completed", "cancelled"]
RankMetric = Literal["period_variance_days", "cumulative_variance_days", "total_budget",
                     "spend_to_date", "spend_pct", "budget_variance", "cumulative_budget_change"]
Row = dict[str, Any]


class InterpretationRule(BaseModel):
    id: str = Field(description="Stable identifier of the domain rule.")
    text: str = Field(description="Guidance for interpreting and reporting this tool's result.")


class Success(BaseModel):
    # Preserve existing and future response fields in both JSON representations.
    model_config = ConfigDict(extra="allow")
    provenance: Row
    interpretation_rules: list[InterpretationRule]

    def model_dump(self, **kwargs):
        # Optional fields absent in the legacy JSON text stay absent in structuredContent.
        kwargs.setdefault("exclude_unset", True)
        return super().model_dump(**kwargs)


class SQLResult(Success):
    rows: list[Row] | None = None
    file: str | None = None
    truncated: bool | None = None


class DatasetInfoResult(Success):
    datasets: list[Row]
    domain_rules: list[str]
    caveats: list[str]


class AgenciesResult(Success):
    agencies: list[Row]


class CategoriesResult(Success):
    categories: list[Row]
    period: str | None = None


class FieldsResult(Success):
    fields: list[Row]


class TableResult(Success):
    tables: list[Row] | None = None
    table: str | None = None
    columns: list[Row] | None = None


class ResolutionResult(Success):
    schedule_matches: list[Row]
    budget_matches: list[Row]


class ScheduleResult(Success):
    answer: Row | None
    linked_budgets: list[Row]


class BudgetResult(Success):
    answer: list[Row]
    linked_schedules: list[Row]


class HistoryResult(Success):
    anchor: Row
    periods: list[Row] | None = None
    lines: list[Row] | None = None


class BreakdownResult(Success):
    groups: list[Row]
    period: str
    metric: str


class ChangesResult(Success):
    changes: list[Row]


class ReasonsResult(Success):
    reasons: list[Row]
    coverage: Row


class BudgetChangeResult(Success):
    target: str
    change: Row


class RankingResult(Success):
    ranked_entity: Literal["schedule", "budget"]
    rank_by: RankMetric
    rows: list[Row]
    label: str


class DurationResult(Success):
    n_projects: int
    excluded_missing_dates: int
    stats: Row | None = None
    groups: list[Row] | None = None


class PortfolioResult(Success):
    rows: list[Row]
    summary: Row
    truncated: bool
    notes: list[str]
