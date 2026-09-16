from pydantic import BaseModel, model_validator


class ResearchEntityRead(BaseModel):
    id: int
    period_id: int | None = None
    type: str
    code: str | None = None
    normalized_code: str | None = None
    name: str | None = None
    normalized_name: str | None = None
    director_name: str | None = None
    normalized_director_name: str | None = None
    year: int | None = None
    cycle: int | None = None
    academic_unit: str | None = None
    career_name: str | None = None
    progress_percentage: float | None = None
    status: str | None = None
    validation_status: str
    source_file: str | None = None
    source_page: int | None = None
    source_section: str | None = None
    confidence_score: float | None = None
    reason: str | None = None
    year_label: str | None = None
    project_validation_status: str | None = None
    director_validation_status: str | None = None
    director_identity_validation_status: str | None = None
    director_relationship_validation_status: str | None = None
    director_canonical_identity_key: str | None = None
    director_canonical_name: str | None = None
    director_identity_reason: str | None = None
    director_relationship_reason: str | None = None
    director_pending_reason: str | None = None
    director_display_status: str | None = None
    display_status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def use_related_period_values(cls, value):
        if isinstance(value, dict):
            return value
        period = getattr(value, "period", None)
        if period is None or getattr(value, "cycle", None) is not None:
            return value
        data = {field: getattr(value, field, None) for field in cls.model_fields}
        data["cycle"] = period.cycle
        data["year_label"] = period.year_label
        return data

    model_config = {"from_attributes": True}
