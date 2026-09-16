from pydantic import BaseModel, Field

from app.models.enums import ProductionType, Quartile


class ProductionBase(BaseModel):
    teacher_id: int | None = None
    period_id: int
    research_entity_id: int | None = None
    production_type: ProductionType
    title: str = Field(min_length=3, max_length=250)
    journal: str | None = None
    quartile: Quartile | None = None
    link: str | None = None
    evidence_url: str | None = None
    status: str = "published"
    validation_status: str = "validated"
    review_reason: str | None = None


class ProductionCreate(ProductionBase):
    pass


class ProductionAuthorRead(BaseModel):
    id: int
    production_id: int
    canonical_identity_key: str
    canonical_name: str
    variants: list[str] = []
    raw_author_name: str | None = None
    normalized_author_name: str | None = None
    person_type: str
    production_role: str = "autor_producto"
    validation_status: str
    identity_source: str | None = None
    identity_confidence: float | None = None
    identity_reason: str | None = None
    confidence: float | None = None
    reason: str | None = None


class ProductionRead(ProductionBase):
    id: int
    canonical_title: str | None = None
    raw_title: str | None = None
    normalized_title: str | None = None
    raw_authors: str | None = None
    normalized_authors: str | None = None
    source_file: str | None = None
    source_section: str | None = None
    source_page: int | None = None
    normalization_reason: str | None = None
    document: str | None = None
    confidence: float | None = None
    reason: str | None = None
    evidence_status: str = "missing"
    kpi_eligible: bool = False
    visibility: str = "pending"
    authors: list[ProductionAuthorRead] = []
    teacher_name: str | None = None
    career_name: str | None = None
    faculty_name: str | None = None
    year_label: str
    cycle: int

    model_config = {"from_attributes": True}
