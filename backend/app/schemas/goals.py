from pydantic import BaseModel, Field

from app.models.enums import GoalMetric


class AnnualGoalCreate(BaseModel):
    career_id: int
    year_label: str
    metric: GoalMetric
    planned_value: int = Field(ge=0)


class AnnualGoalRead(AnnualGoalCreate):
    id: int

    model_config = {"from_attributes": True}


class GoalSummaryItem(BaseModel):
    metric: GoalMetric
    label: str
    planned_value: int
    reported_value: int
    compliance_percent: float
